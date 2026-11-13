"""Read and write UEFN landscape spline clipboard text (BEGIN_SPLINES ... END_SPLINES).

A control point keeps its location, rotation, width, falloff, mesh and terrain flags;
a segment keeps its two connections (point index + tangent length) and spline mesh.
Every line the parser does not model is kept verbatim, so a round trip reproduces the
actor exactly and only edited values change.
"""

from __future__ import annotations

import math
import re

POINT_CLASS = "/Script/Landscape.LandscapeSplineControlPoint"
SEGMENT_CLASS = "/Script/Landscape.LandscapeSplineSegment"

_BEGIN = re.compile(r'Begin Object Class=(\S+) Name="(\w+?)_(\d+)"')
_LOC = re.compile(r"Location=\(X=([-\d.eE+]+),Y=([-\d.eE+]+),Z=([-\d.eE+]+)\)")
_ROT = re.compile(r"Rotation=\(Pitch=([-\d.eE+]+),Yaw=([-\d.eE+]+),Roll=([-\d.eE+]+)\)")
_NUM = re.compile(r"^(\w+)=([-\d.eE+]+)$")
_CONN = re.compile(r"Connections\((\d)\)=\(ControlPoint=\"[^']*'LandscapeSplineControlPoint_(\d+)'\",TangentLen=([-\d.eE+]+)\)")
_MESH = re.compile(r"Mesh=\"/Script/Engine.StaticMesh'([^']+)'\"")


def _short(path: str) -> str:
    return path.rsplit(".", 1)[-1] if path else ""


def parse(text: str) -> dict:
    """Text -> {points: [...], segments: [...]} (points keep their index i)."""
    points, segments = [], []
    cur = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line in ("BEGIN_SPLINES", "END_SPLINES") or line.startswith("SPLINE LOCATION"):
            continue
        m = _BEGIN.match(line)
        if m:
            cls, _, idx = m.groups()
            if cls == POINT_CLASS:
                cur = {"i": int(idx), "x": 0.0, "y": 0.0, "z": 0.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0,
                       "w": 500.0, "side": 500.0, "mesh": "", "raise": False, "lines": []}
                points.append(cur)
            elif cls == SEGMENT_CLASS:
                cur = {"i": int(idx), "a": -1, "b": -1, "ta": 0.0, "tb": 0.0, "mesh": "", "raise": False, "lines": []}
                segments.append(cur)
            else:
                cur = None
            continue
        if line == "End Object" or cur is None:
            cur = None if line == "End Object" else cur
            continue
        if "a" in cur:  # segment
            c = _CONN.match(line)
            if c:
                k, pid, tl = c.groups()
                if k == "0":
                    cur["a"], cur["ta"] = int(pid), float(tl)
                else:
                    cur["b"], cur["tb"] = int(pid), float(tl)
                continue
            mm = _MESH.search(line)
            if line.startswith("SplineMeshes") and mm:
                cur["mesh"] = _short(mm.group(1))
            if line.startswith("bRaiseTerrain="):
                cur["raise"] = line.endswith("True")
            cur["lines"].append(line)
            continue
        lm = _LOC.match(line)
        if lm:
            cur["x"], cur["y"], cur["z"] = (float(v) for v in lm.groups())
            continue
        rm = _ROT.match(line)
        if rm:
            cur["pitch"], cur["yaw"], cur["roll"] = (float(v) for v in rm.groups())
            continue
        nm = _NUM.match(line)
        if nm and nm.group(1) == "Width":
            cur["w"] = float(nm.group(2))
        if nm and nm.group(1) == "SideFalloff":
            cur["side"] = float(nm.group(2))
        mm = _MESH.match(line)
        if mm:
            cur["mesh"] = _short(mm.group(1))
        if line.startswith("bRaiseTerrain="):
            cur["raise"] = line.endswith("True")
        cur["lines"].append(line)
    return {"points": points, "segments": segments}


def forward(p: dict) -> tuple:
    """Unit vector of a control point's rotation (pitch, yaw in degrees)."""
    cp, sp = math.cos(math.radians(p["pitch"])), math.sin(math.radians(p["pitch"]))
    cy, sy = math.cos(math.radians(p["yaw"])), math.sin(math.radians(p["yaw"]))
    return (cp * cy, cp * sy, sp)


def set_terrain_flags(lines: list, on: bool) -> list:
    val = "True" if on else "False"
    out = []
    for line in lines:
        if line.startswith("bRaiseTerrain="):
            line = "bRaiseTerrain=" + val
        elif line.startswith("bLowerTerrain="):
            line = "bLowerTerrain=" + val
        out.append(line)
    return out


def serialize(points: list, segments: list, terrain: str = "") -> str:
    """Points + segments -> clipboard text. terrain 'off' forces Raise/Lower off everywhere."""
    out = ["BEGIN_SPLINES", "SPLINE LOCATION: X=0.000000 Y=0.000000 Z=0.000000"]
    for p in sorted(points, key=lambda q: q["i"]):
        lines = set_terrain_flags(p.get("lines") or [], False) if terrain == "off" else (p.get("lines") or [])
        out.append(f'Begin Object Class={POINT_CLASS} Name="LandscapeSplineControlPoint_{p["i"]}"')
        out.append(f'   Location=(X={p["x"]:.3f},Y={p["y"]:.3f},Z={p["z"]:.3f})')
        out.append(f'   Rotation=(Pitch={p["pitch"]:.4f},Yaw={p["yaw"]:.4f},Roll={p["roll"]:.4f})')
        out.extend("   " + line for line in lines)
        out.append("End Object")
    for s in sorted(segments, key=lambda q: q["i"]):
        lines = set_terrain_flags(s.get("lines") or [], False) if terrain == "off" else (s.get("lines") or [])
        out.append(f'Begin Object Class={SEGMENT_CLASS} Name="LandscapeSplineSegment_{s["i"]}"')
        out.append(f"   Connections(0)=(ControlPoint=\"{POINT_CLASS}'LandscapeSplineControlPoint_{s['a']}'\",TangentLen={s['ta']:.3f})")
        out.append(f"   Connections(1)=(ControlPoint=\"{POINT_CLASS}'LandscapeSplineControlPoint_{s['b']}'\",TangentLen={s['tb']:.3f})")
        out.extend("   " + line for line in lines)
        out.append("End Object")
    out.append("END_SPLINES")
    return "\r\n".join(out)


def move_point(points: list, segments: list, index: int, x: float, y: float, z: float) -> dict | None:
    """Move one point and rescale the tangents of the segments that touch it."""
    by_i = {p["i"]: p for p in points}
    p = by_i.get(index)
    if p is None:
        return None
    p["x"], p["y"], p["z"] = float(x), float(y), float(z)
    for s in segments:
        if index in (s["a"], s["b"]):
            a, b = by_i.get(s["a"]), by_i.get(s["b"])
            if not a or not b:
                continue
            length = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])) or 1.0
            s["ta"] = math.copysign(length, s["ta"] or 1.0)
            s["tb"] = math.copysign(length, s["tb"] or -1.0)
    return p


SPIKE_GRADE = 0.18  # no street piece steeper than this, whatever max_cut says


def drape(points: list, segments: list, ground: dict, iters: int = 4, pin_joints: bool = True,
          max_grade: dict | None = None, max_cut: float | None = None) -> dict:
    """Set every live (Raise/Lower) point onto the ground, smoothed along the roads.

    ground: {point index: base ground z}. Junction groups (points joined by mesh-less links)
    stay flat at their mean height; live points touching a non-live point (bridge, deck)
    keep their height so the joint holds. Pitch follows the new slope and each touched
    segment's tangents scale with its new chord, so curves keep their shape.
    max_grade: {point index: steepest rise / run} - roads are graded like real ones (arterial 4 %, local
    8 %): where a live segment is steeper, its ends are pulled toward each other (cut / fill: the live
    spline then reshapes the terrain), so a road never climbs every bump the ground has.
    """
    by_i = {p["i"]: p for p in points}
    # pin_joints=False drapes every point that has ground (ground-level kit pieces, bridge ends)
    live = {p["i"] for p in points if (p["raise"] or not pin_joints) and p["i"] in ground}
    adj: dict = {i: set() for i in by_i}
    parent = {i: i for i in by_i}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for s in segments:
        a, b = s["a"], s["b"]
        if a in by_i and b in by_i:
            adj[a].add(b)
            adj[b].add(a)
            if not s["mesh"] and a in live and b in live:
                parent[find(a)] = find(b)
    fixed = {i for i in live if any(n not in live for n in adj[i])}
    groups: dict = {}
    for i in live:
        groups.setdefault(find(i), []).append(i)
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    z = {i: by_i[i]["z"] for i in by_i}
    for i in live - fixed:
        z[i] = float(ground[i])

    def pin_groups():
        for members in groups.values():
            pins = [z[i] for i in members if i in fixed]
            h = sum(pins) / len(pins) if pins else sum(z[i] for i in members) / len(members)
            for i in members:
                z[i] = h

    in_group = {i for v in groups.values() for i in v}
    pin_groups()
    for _ in range(max(0, iters)):
        nz = dict(z)
        for i in live - fixed - in_group:
            if adj[i]:
                nz[i] = 0.5 * z[i] + 0.5 * sum(z[n] for n in adj[i]) / len(adj[i])
        z = nz
        pin_groups()
    def grade_pass(least: float = 0.0):
        for _ in range(60):
            moved = 0.0
            for s in segments:
                a, b = s["a"], s["b"]
                if a not in live or b not in live or a not in max_grade or b not in max_grade:
                    continue
                run = math.hypot(by_i[a]["x"] - by_i[b]["x"], by_i[a]["y"] - by_i[b]["y"])
                over = abs(z[b] - z[a]) - max(least, min(max_grade[a], max_grade[b])) * run
                if over <= 1.0:
                    continue
                # (in the last, anti-wall pass two points both held at a bridge's ends share the step too: three of
                # them 15 m apart on a lake's bank stood 7 m apart in height, a 55 % boulevard)
                free = [i for i in (a, b) if i not in fixed] or ([a, b] if least else [])
                if not free:
                    continue
                step = over / len(free)
                for i in free:  # the high end comes down, the low end comes up
                    z[i] += -step if z[i] > z[a if i == b else b] else step
                moved += over
            pin_groups()
            if moved < 1.0:
                break
    if max_grade:
        grade_pass()
    if max_cut:  # a road is never cut or filled deeper than this: across a valley it takes a steeper grade instead
        for i in live - fixed:
            g = float(ground[i])
            z[i] = min(max(z[i], g - max_cut), g + max_cut)
        pin_groups()
        if max_grade:
            # ... steeper, never a wall: that clamp alone left single points 7 m off both their neighbours (a junction
            # at a lake's edge, 55 % streets, Nov 12 2026). Past SPIKE_GRADE the cut or fill goes deeper instead
            grade_pass(SPIKE_GRADE)
    shifts = []
    old = {i: (by_i[i]["x"], by_i[i]["y"], by_i[i]["z"]) for i in by_i}
    for i in live:
        shifts.append(abs(z[i] - by_i[i]["z"]))
        by_i[i]["z"] = z[i]
    for i in live:
        p = by_i[i]
        if i in in_group:
            p["pitch"] = 0.0
            continue
        f = forward({"pitch": 0.0, "yaw": p["yaw"]})
        ahead, behind = [], []
        for n in adj[i]:
            q = by_i[n]
            dx, dy = q["x"] - p["x"], q["y"] - p["y"]
            (ahead if dx * f[0] + dy * f[1] >= 0 else behind).append(q)
        za = ahead[0] if ahead else p
        zb = behind[0] if behind else p
        dh = math.dist((za["x"], za["y"]), (zb["x"], zb["y"]))
        if dh > 1:
            p["pitch"] = math.degrees(math.atan2(za["z"] - zb["z"], dh))
    for s in segments:
        if s["a"] in live or s["b"] in live:
            a, b = by_i.get(s["a"]), by_i.get(s["b"])
            if not a or not b:
                continue
            before = math.dist(old[s["a"]], old[s["b"]])
            after = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
            # Short links (points nearly on top of each other) must not scale: a 1 cm chord
            # turned a 10 m tangent into 1 km and threw the spline into the sky.
            ratio = min(2.0, max(0.5, after / before)) if before >= 100 else 1.0
            cap = 2 * after + 100
            for key in ("ta", "tb"):
                s[key] = math.copysign(min(abs(s[key]) * ratio, cap), s[key] or 1.0)
    shifts.sort()
    n = len(shifts)
    return {"draped": n, "groups": len(groups), "kept_at_joints": len(fixed),
            "median_shift_cm": round(shifts[n // 2]) if n else 0, "max_shift_cm": round(shifts[-1]) if n else 0}


def sample_grid(terrain: dict, x: float, y: float):
    """Bilinear ground height from a scan grid ({min, max, grid, heights[j][i]}); None off it."""
    n = int(terrain.get("grid") or 0)
    rows = terrain.get("heights") or []
    if n < 2 or len(rows) < n:
        return None
    lo, hi = terrain["min"], terrain["max"]
    fx = (x - lo[0]) / (hi[0] - lo[0]) * n - 0.5
    fy = (y - lo[1]) / (hi[1] - lo[1]) * n - 0.5
    i0 = max(0, min(n - 2, int(math.floor(fx))))
    j0 = max(0, min(n - 2, int(math.floor(fy))))
    tx, ty = max(0.0, min(1.0, fx - i0)), max(0.0, min(1.0, fy - j0))
    c = [rows[j0][i0], rows[j0][i0 + 1], rows[j0 + 1][i0], rows[j0 + 1][i0 + 1]]
    w = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty]
    pairs = [(v, k) for v, k in zip(c, w) if v is not None]
    total = sum(k for _, k in pairs)
    if not pairs:
        return None
    if total <= 1e-9:  # only zero-weight corners are known: use them plainly
        return sum(v for v, _ in pairs) / len(pairs)
    return sum(v * k for v, k in pairs) / total


def grid_sampler(terrain: dict):
    """sample_grid() for one terrain as (x, y) -> height: the same numbers, its setup done once (a city
    preview samples the ground a million times)."""
    n = int(terrain.get("grid") or 0)
    rows = terrain.get("heights") or []
    if n < 2 or len(rows) < n:
        return lambda x, y: None
    lo, hi = terrain["min"], terrain["max"]
    lo0, lo1, w, d = lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1]
    floor, top = math.floor, n - 2

    def sample(x: float, y: float):
        fx = (x - lo0) / w * n - 0.5
        fy = (y - lo1) / d * n - 0.5
        i0 = max(0, min(top, int(floor(fx))))
        j0 = max(0, min(top, int(floor(fy))))
        tx, ty = max(0.0, min(1.0, fx - i0)), max(0.0, min(1.0, fy - j0))
        r0, r1 = rows[j0], rows[j0 + 1]
        a, b, c, e = r0[i0], r0[i0 + 1], r1[i0], r1[i0 + 1]
        if a is None or b is None or c is None or e is None:
            return sample_grid(terrain, x, y)  # a hole in the scan: the general case
        w0, w1, w2, w3 = (1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty
        total = sum((w0, w1, w2, w3))  # sum() itself: its float rounding is sample_grid's
        if total <= 1e-9:
            return sum((a, b, c, e)) / 4
        return sum((a * w0, b * w1, c * w2, e * w3)) / total
    return sample


GIANT_CM = 8000  # a junction piece or join link longer than 80 m is broken data, not a road


def _set_line(lines: list, key: str, value: str) -> list:
    out = [ln for ln in lines if not ln.startswith(key + "=")]
    return out + [f"{key}={value}"] if value is not None else out


def problems(points: list, segments: list) -> dict:
    """Broken data that would stamp giant shapes into the terrain."""
    by_i = {p["i"]: p for p in points}
    giant_nodes = [{"point": p["i"], "mesh": p["mesh"], "width_cm": round(p["w"])}
                   for p in points if p["w"] > GIANT_CM]
    long_links = []
    for s in segments:
        a, b = by_i.get(s["a"]), by_i.get(s["b"])
        if a and b and not s["mesh"]:
            d = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
            if d > GIANT_CM:
                long_links.append({"segment": s["i"], "a": s["a"], "b": s["b"], "length_cm": round(d)})
    return {"giant_nodes": giant_nodes, "long_links": long_links, "bad_tangents": bad_tangents(points, segments)}


def repair(points: list, segments: list, road_half_width: float = 1400.0) -> dict:
    """Drop join links longer than GIANT_CM, and strip the piece off every node they or a giant
    Width touch (its mesh was built across that link). The node keeps its other links as a
    plain shared point with a normal road width."""
    found = problems(points, segments)
    drop = {l["segment"] for l in found["long_links"]}
    broken = {n["point"] for n in found["giant_nodes"]}
    for s in segments:
        if s["i"] in drop:
            broken.update(p for p in (s["a"], s["b"]) if (next((q for q in points if q["i"] == p), {}).get("mesh") or "").startswith("SM_Junc_"))
    segments[:] = [s for s in segments if s["i"] not in drop]
    for p in points:
        if p["i"] in broken:
            p["mesh"] = ""
            p["w"] = min(p["w"], road_half_width)
            lines = [ln for ln in p["lines"] if not ln.startswith(("Mesh=", "MeshScale=", "MaterialOverrides="))]
            p["lines"] = _set_line(lines, "Width", f"{p['w']:.1f}")
    return {"links_dropped": len(drop), "nodes_fixed": len(broken),
            "left": {k: len(v) for k, v in problems(points, segments).items()}}



LEAD_IN_CM = 1300.0  # the old junction recipe put a lead-in point ~9 m out on each arm


def _travel_dir(p: dict, toward: dict, sign_ref: dict) -> tuple:
    """p's forward (with pitch), flipped to point along the travel direction sign_ref -> toward."""
    f = forward(p)
    d = (toward["x"] - sign_ref["x"], toward["y"] - sign_ref["y"])
    return f if f[0] * d[0] + f[1] * d[1] >= 0 else (-f[0], -f[1], -f[2])


def _hermite_fits(chain: list, a: int, b: int, tol_xy: float, tol_z: float) -> bool:
    """Does the spline from chain[a] to chain[b] (end directions = the points' own facing, tangent
    length = chord, as the paste uses) pass within tolerance of every point between them?"""
    pa, pb = chain[a], chain[b]
    fa = _travel_dir(pa, pb, pa)
    fb = _travel_dir(pb, pb, pa)
    chord = math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"])) or 1.0
    curve = []
    for k in range(33):
        t = k / 32
        t2, t3 = t * t, t * t * t
        h00, h10, h01, h11 = 2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t, -2 * t3 + 3 * t2, t3 - t2
        curve.append(tuple(h00 * pa[c] + h10 * chord * fa[n] + h01 * pb[c] + h11 * chord * fb[n]
                           for n, c in enumerate(("x", "y", "z"))))
    for q in chain[a + 1:b]:
        if q.get("_lead_in"):
            continue  # a generator helper point, not part of the road's real line
        best_xy, best_z = 1e18, 1e18
        for (x0, y0, z0), (x1, y1, z1) in zip(curve, curve[1:]):
            dx, dy = x1 - x0, y1 - y0
            L2 = dx * dx + dy * dy or 1e-9
            t = max(0.0, min(1.0, ((q["x"] - x0) * dx + (q["y"] - y0) * dy) / L2))
            dxy = math.hypot(q["x"] - (x0 + t * dx), q["y"] - (y0 + t * dy))
            if dxy < best_xy:
                best_xy, best_z = dxy, abs(q["z"] - (z0 + t * (z1 - z0)))
        if best_xy > tol_xy or best_z > tol_z:
            return False
    return True


def _hermite_keep(chain: list, tol_xy: float, tol_z: float, max_len: float) -> list:
    """Greedy: from each kept point, jump to the farthest point the spline can reach while still
    following the road. A straight road between two junctions keeps only its two ends."""
    keep, i, n = [0], 0, len(chain)
    while i < n - 1:
        # nearest point that is not a lead-in: the fallback when nothing farther fits
        j = next((k for k in range(i + 1, n) if not chain[k].get("_lead_in")), n - 1)
        for k in range(n - 1, j, -1):
            if chain[k].get("_lead_in"):
                continue
            far = math.dist((chain[i]["x"], chain[i]["y"]), (chain[k]["x"], chain[k]["y"]))
            if far <= max_len and _hermite_fits(chain, i, k, tol_xy, tol_z):
                j = k
                break
        keep.append(j)
        i = j
    return keep


def thin(points: list, segments: list, tol_xy: float = 50.0, tol_z: float = 30.0, max_len: float = 50000.0) -> dict:
    """Drop road points that only add spacing: a chain of plain points (2 segments, same mesh, no
    piece) keeps its ends plus the points Douglas-Peucker needs (bends over tol_xy, slope changes
    over tol_z), and no segment longer than max_len. Merged segments keep the first segment's
    mesh and settings; tangents = the new chord, signs kept."""
    by_i = {p["i"]: p for p in points}
    segs_at: dict = {}
    for s in segments:
        segs_at.setdefault(s["a"], []).append(s)
        segs_at.setdefault(s["b"], []).append(s)

    def plain(i):
        ss = segs_at.get(i, [])
        return (len(ss) == 2 and not by_i[i]["mesh"] and ss[0]["mesh"] and ss[0]["mesh"] == ss[1]["mesh"])

    used, new_segs, dropped = set(), [], set()
    for s0 in segments:
        if s0["i"] in used:
            continue
        # walk to a chain end on both sides, starting from s0
        chain_pts, chain_segs = [s0["a"], s0["b"]], [s0]
        used.add(s0["i"])
        for head in (True, False):
            while True:
                end = chain_pts[0] if head else chain_pts[-1]
                if not plain(end):
                    break
                nxt = next((s for s in segs_at[end] if s["i"] not in used), None)
                if nxt is None:
                    break
                used.add(nxt["i"])
                other = nxt["b"] if nxt["a"] == end else nxt["a"]
                if head:
                    chain_pts.insert(0, other)
                    chain_segs.insert(0, nxt)
                else:
                    chain_pts.append(other)
                    chain_segs.append(nxt)
        if len(chain_segs) == 1:
            new_segs.append(s0)
            continue
        xyz = [(by_i[i]["x"], by_i[i]["y"], by_i[i]["z"]) for i in chain_pts]
        # the old generator's lead-in points (a plain point within 13 m of a junction mouth) are never
        # needed: the mouth's end tangent already enters square, so they are left out of the fit
        for end, nxt in ((0, 1), (len(chain_pts) - 1, len(chain_pts) - 2)):
            if 0 < nxt < len(chain_pts) - 1 and any(not s["mesh"] for s in segs_at.get(chain_pts[end], [])) \
                    and math.dist(xyz[end][:2], xyz[nxt][:2]) < LEAD_IN_CM:
                by_i[chain_pts[nxt]]["_lead_in"] = True
        keep = _hermite_keep([by_i[i] for i in chain_pts], tol_xy, tol_z, max_len)
        for i in chain_pts:
            by_i[i].pop("_lead_in", None)
        # never leave a segment longer than max_len
        k2 = [keep[0]]
        for k in keep[1:]:
            while math.dist(xyz[k2[-1]], xyz[k]) > max_len:
                k2.append(k2[-1] + max(1, int((k - k2[-1]) / 2)))
            k2.append(k)
        keep = sorted(set(k2))
        for a, b in zip(keep, keep[1:]):
            first, last = chain_segs[a], chain_segs[b - 1]
            pa, pb = chain_pts[a], chain_pts[b]
            # orientation of the original segments decides which end is "a"
            sa = first if first["a"] == pa or first["b"] == pa else first
            ta_sign = math.copysign(1.0, (sa["ta"] if sa["a"] == pa else sa["tb"]) or 1.0)
            tb_sign = math.copysign(1.0, (last["tb"] if last["b"] == pb else last["ta"]) or -1.0)
            chord = math.dist(xyz[a], xyz[b]) or 1.0
            merged = dict(first)
            merged["a"], merged["b"] = pa, pb
            merged["ta"], merged["tb"] = ta_sign * chord, tb_sign * chord
            new_segs.append(merged)
        dropped.update(chain_pts[k] for k in range(len(chain_pts)) if k not in keep)
    segments[:] = new_segs
    points[:] = [p for p in points if p["i"] not in dropped]
    return {"points_removed": len(dropped), "points": len(points), "segments": len(segments)}


def tune(points: list, segments: list, width: float | None = None, side: float = 800.0, end: float = 1000.0,
         mesh_offset: float | None = None, paint_layer: str = "") -> dict:
    """Terrain settings for every live road point: SideFalloff, EndFalloff, Width (when given; junction
    pieces keep theirs) and Mesh Vertical Offset. paint_layer: landscape paint layer the splines paint
    under the road (through the Splines edit layer), e.g. a grass-free asphalt layer."""
    if paint_layer:
        for s in segments:
            if s["raise"] and s["mesh"]:
                s["lines"] = _set_line(s["lines"], "LayerName", paint_layer)
        for p in points:
            if p["raise"]:
                p["lines"] = _set_line(p["lines"], "LayerName", paint_layer)
    n = 0
    for p in points:
        if not p["raise"]:
            continue
        lines = _set_line(p["lines"], "SideFalloff", f"{side:.6f}")
        lines = _set_line(lines, "EndFalloff", f"{end:.6f}")
        if width is not None and not p["mesh"]:
            p["w"] = width
            lines = _set_line(lines, "Width", f"{width:.1f}")
        if mesh_offset is not None:
            lines = _set_line(lines, "SegmentMeshOffset", f"{mesh_offset:.1f}")
        p["side"] = side
        p["lines"] = lines
        n += 1
    return {"points_tuned": n, "side_falloff": side, "end_falloff": end}


# OUR road families, measured from the straights' end profiles (cm; tile from x=0 to its length).
# bands: (from_offset, to_offset, top_z, material) out from the centre line, mirrored on both sides;
# lines: (offset_from, offset_to, material) painted on the asphalt; skirt: bottom of the side walls.
OUR_FAMILIES = {
    "2L": {"mesh": "SM_Road_TwoLane_Straight10m", "tile": 1000, "half": 525, "skirt": -40,
           "materials": ["M_Asphalt", "M_PaintW", "M_PaintY"],
           "bands": [(0, 525, 0, "M_Asphalt")],
           "lines": [(485, 505, "M_PaintW"), (0, 10, "M_PaintY")]},
    "2S": {"mesh": "SM_Road_TwoLaneSidewalk_Straight10m", "tile": 1000, "half": 875, "skirt": -40,
           "materials": ["M_Asphalt", "M_Gutter", "M_Curb", "M_PaverBand", "M_Pavers", "M_Grate", "M_PaintY"],
           "bands": [(0, 480, 0, "M_Asphalt"), (480, 525, -2, "M_Gutter"), (525, 555, 17, "M_Curb"),
                     (555, 615, 16, "M_PaverBand"), (615, 855, 16, "M_Pavers"), (855, 875, 17, "M_Curb")],
           "lines": [(8, 22, "M_PaintY")]},
    "4L": {"mesh": "SM_Road_FourLane_Straight10m", "tile": 1000, "half": 1400, "skirt": -40,
           "materials": ["M_Asphalt", "M_Gutter", "M_Curb", "M_PaverBand", "M_Pavers", "M_Grate", "M_PaintY", "M_PaintW"],
           "bands": [(0, 1005, 0, "M_Asphalt"), (1005, 1050, -2, "M_Gutter"), (1050, 1080, 17, "M_Curb"),
                     (1080, 1140, 16, "M_PaverBand"), (1140, 1380, 16, "M_Pavers"), (1380, 1400, 17, "M_Curb")],
           "lines": [(515, 535, "M_PaintW"), (8, 22, "M_PaintY")]},
    "FW": {"mesh": "SM_Road_Freeway_Lane20m", "tile": 2000, "half": 1650, "skirt": -40,
           "materials": ["M_Asphalt", "M_PaintY", "M_PaintW", "M_BarrierConcrete"],
           "bands": [(30, 1590, 0, "M_Asphalt")],
           "barriers": [(0, 30, 85), (1590, 1650, 85)],
           "lines": [(203, 218, "M_PaintY"), (740, 760, "M_PaintW"), (1280, 1300, "M_PaintW")]},
}

KIT_DIR = "/Game/Creative/Environments/Meshes/Roads"
KIT_ROAD = "Road1_Straight"
KIT_HALF_WIDTH = 600.0  # Road1 kerb to kerb is 2 x 545, skirt to 595
# Open ends of the 3-way kit pieces, measured from their boundary edges:
# (end centre x, end centre y, outward direction in degrees) in the piece's own frame.
KIT_3WAY = {
    "Road1_T_Section": {"width": 620.0, "ends": [(0.0, 595.0, 90.0), (0.0, -595.0, -90.0), (-595.0, 0.0, 180.0)]},
    "Road1_Y_Split": {"width": 1150.0, "ends": [(-1536.0, 0.0, 180.0), (676.0, 597.0, 23.3), (676.0, -597.0, -23.3)]},
    "Road1_Split": {"width": 1500.0, "ends": [(-1024.0, 0.0, 180.0), (2048.0, 0.0, 0.0), (1228.0, 1086.0, 45.5)]},
}
KIT_MAX_ERR = 25.0  # degrees an arm may turn to meet a kit piece


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _mesh_line(name: str) -> str:
    return f"Mesh=\"/Script/Engine.StaticMesh'{KIT_DIR}/{name}.{name}'\""


def retangent(by_i: dict, s: dict) -> None:
    """Tangents = the 3D chord, signed by each point's facing (+ when it faces the other end)."""
    a, b = by_i[s["a"]], by_i[s["b"]]
    chord = max(100.0, math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])))
    for key, p, q in (("ta", a, b), ("tb", b, a)):
        f = forward({"pitch": 0.0, "yaw": p["yaw"]})
        s[key] = chord if f[0] * (q["x"] - p["x"]) + f[1] * (q["y"] - p["y"]) > 0 else -chord


STRAIGHT_XY_CM = 150.0  # a run point this close to the straight line in plan is dropped...
STRAIGHT_Z_CM = 200.0   # ...and, on a deck or ramp (never shaping the ground), this close in height
# A road on the ground keeps a point only where it turns: from corner to corner it runs dead straight and the
# landscape's Splines layer cuts / fills the ground to it (a point per bump made every street wave up and down).


def straighten(points: list, segments: list, xy_tol: float = STRAIGHT_XY_CM, z_tol: float = STRAIGHT_Z_CM) -> tuple:
    """Copies of (points, segments) with every run of plain road points cut to the fewest straight segments.
    A run goes from one kept point (junction, piece, end, bridge / kind change) to the next through points
    with exactly two segments of the same kind; a point stays only where the road really bends (further than
    xy_tol off the straight line in plan, or z_tol in height). A road drawn through a point every few metres,
    each draped on its own bump, waved; two points and one segment run straight (the Splines layer shapes the
    ground to it)."""
    pts = {p["i"]: dict(p) for p in points}
    segs = [dict(s) for s in segments]
    at: dict = {}
    for s in segs:
        at.setdefault(s["a"], []).append(s)
        at.setdefault(s["b"], []).append(s)

    held = {i for p in pts.values() for s in ((p.get("undo") or {}).get("segments") or []) for i in (s["a"], s["b"])}

    def plain(i):  # (a point a join's undo needs to put a road back stays)
        two = at.get(i, [])
        p = pts[i]
        return (i not in held and len(two) == 2 and two[0]["lines"] == two[1]["lines"] and not any(l.startswith("Mesh=") for l in p.get("lines") or [])
                and p.get("raise") == pts[two[0]["b"] if two[0]["a"] == i else two[0]["a"]].get("raise"))

    def far(run, lo, hi):  # index of the run point furthest off the lo-hi line, and whether it is off it
        a, b = pts[run[lo]], pts[run[hi]]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        L = math.hypot(dx, dy) or 1.0
        best, worst = None, 0.0
        for k in range(lo + 1, hi):
            p = pts[run[k]]
            t = max(0.0, min(1.0, ((p["x"] - a["x"]) * dx + (p["y"] - a["y"]) * dy) / (L * L)))
            off_xy = abs((p["x"] - a["x"]) * dy - (p["y"] - a["y"]) * dx) / L
            off_z = abs(p["z"] - (a["z"] + (b["z"] - a["z"]) * t))
            score = max(off_xy / xy_tol, 0.0 if a.get("raise", True) else off_z / z_tol)
            if score > worst:
                best, worst = k, score
        return best, worst > 1.0

    def keep_of(run):
        keep, stack = {0, len(run) - 1}, [(0, len(run) - 1)]
        while stack:
            lo, hi = stack.pop()
            k, off = far(run, lo, hi)
            if k is not None and off:
                keep.add(k)
                stack += [(lo, k), (k, hi)]
        return sorted(keep)

    done, new_segs, drop = set(), [], set()
    for start in list(pts):
        if plain(start):
            continue
        for s0 in list(at.get(start, [])):
            if id(s0) in done:
                continue
            run, run_segs, cur, s = [start], [], start, s0
            while True:
                done.add(id(s))
                run_segs.append(s)
                cur = s["b"] if s["a"] == cur else s["a"]
                run.append(cur)
                if not plain(cur):
                    break
                s = next(x for x in at[cur] if x is not s)
            if len(run) < 3:
                new_segs.append(s0)
                continue
            keep = keep_of(run)
            for u, v in zip(keep, keep[1:]):
                tmpl = run_segs[u]
                forward_ = tmpl["a"] == run[u]
                ns = dict(tmpl, a=run[u] if forward_ else run[v], b=run[v] if forward_ else run[u])
                new_segs.append(ns)
            drop.update(run[k] for k in range(1, len(run) - 1) if k not in keep)
    new_segs += [s for s in segs if id(s) not in done]  # loops of plain points stay as they were
    out_pts = [p for i, p in pts.items() if i not in drop]
    by_i = {p["i"]: p for p in out_pts}
    for s in new_segs:
        if s["a"] in by_i and s["b"] in by_i:
            retangent(by_i, s)
    return out_pts, [s for s in new_segs if s["a"] in by_i and s["b"] in by_i]


def fit_3way(arm_angles: list) -> tuple:
    """Best kit piece for three arm directions: (piece, yaw, [end index per arm], error) or (None, ...)."""
    import itertools
    best = (None, 0.0, [], 999.0)
    for name, kit in KIT_3WAY.items():
        dirs = [e[2] for e in kit["ends"]]
        for perm in itertools.permutations(range(3)):
            diffs = [_wrap(arm_angles[k] - dirs[j]) for k, j in enumerate(perm)]
            yaw = math.degrees(math.atan2(sum(math.sin(math.radians(d)) for d in diffs),
                                          sum(math.cos(math.radians(d)) for d in diffs)))
            err = max(abs(_wrap(d - yaw)) for d in diffs)
            if err < best[3]:
                best = (name, yaw, list(perm), err)
    return best if best[3] <= KIT_MAX_ERR else (None,) + best[1:]


def kit_convert(points: list, segments: list) -> dict:
    """Move a network onto the Fortnite Road1 kit: road tiles become Road1_Straight (own material,
    Width 600, no mesh offset); 3-way junctions whose arms fit become T_Section / Y_Split / Split,
    their mouths moved onto the piece's measured ends. 4+ way and unfit nodes are reported."""
    by_i = {p["i"]: p for p in points}
    links: dict = {}
    for s in segments:
        if not s["mesh"]:
            links.setdefault(s["a"], []).append(s)
            links.setdefault(s["b"], []).append(s)
    road_segs = 0
    for s in segments:
        if s["mesh"].startswith("SM_Road_") and "Bridge" not in s["mesh"] and "Loop" not in s["mesh"]:
            s["mesh"] = KIT_ROAD
            lines = [ln for ln in s["lines"] if not ln.startswith("SplineMeshes(")]
            s["lines"] = lines + [f"SplineMeshes(0)=({_mesh_line(KIT_ROAD)},bScaleToWidth=False)"]
            road_segs += 1
    counts = {name: 0 for name in KIT_3WAY}
    unfit: list = []
    touched = set()
    for p in points:
        if not p["mesh"].startswith("SM_Junc_") or p["mesh"] == "SM_Junc_Empty":
            continue
        arms = [(s["b"] if s["a"] == p["i"] else s["a"]) for s in links.get(p["i"], [])]
        if len(arms) in (1, 2):
            # corners, width changes and dead ends need no piece once every road is Road1:
            # the road simply runs through the node
            for s in links.get(p["i"], []):
                s["mesh"] = KIT_ROAD
                s["lines"] = [ln for ln in s["lines"] if not ln.startswith("SplineMeshes(")] + \
                             [f"SplineMeshes(0)=({_mesh_line(KIT_ROAD)},bScaleToWidth=False)"]
            if len(arms) == 2:
                a, b = by_i[arms[0]], by_i[arms[1]]
                p["yaw"] = math.degrees(math.atan2(b["y"] - a["y"], b["x"] - a["x"]))
            p["mesh"], p["pitch"], p["w"] = "", 0.0, KIT_HALF_WIDTH
            p["lines"] = [ln for ln in p["lines"] if not ln.startswith(("Mesh=", "MeshScale=", "MaterialOverrides="))]
            touched.update([p["i"], *arms])
            counts["through"] = counts.get("through", 0) + 1
            continue
        if len(arms) != 3:
            unfit.append({"point": p["i"], "arms": len(arms), "why": "not 3-way"})
            continue
        angles = [math.degrees(math.atan2(by_i[m]["y"] - p["y"], by_i[m]["x"] - p["x"])) for m in arms]
        name, yaw, perm, err = fit_3way(angles)
        if not name:
            unfit.append({"point": p["i"], "arms": 3, "why": f"arms off by {err:.0f} deg"})
            continue
        kit = KIT_3WAY[name]
        c, s_ = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        for k, m in enumerate(arms):
            ex, ey, ed = kit["ends"][perm[k]]
            q = by_i[m]
            q["x"], q["y"], q["z"] = p["x"] + c * ex - s_ * ey, p["y"] + s_ * ex + c * ey, p["z"]
            q["yaw"], q["pitch"] = _wrap(yaw + ed), 0.0
            touched.add(m)
        p["mesh"], p["yaw"], p["pitch"], p["w"] = name, _wrap(yaw), 0.0, kit["width"]
        lines = [ln for ln in p["lines"] if not ln.startswith(("Mesh=", "MeshScale=", "MaterialOverrides="))]
        p["lines"] = _set_line(lines + [_mesh_line(name), "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"],
                               "Width", f"{kit['width']:.1f}")
        touched.add(p["i"])
        counts[name] += 1
    for p in points:
        if p["raise"] and not p["mesh"]:
            p["w"] = KIT_HALF_WIDTH
            p["lines"] = _set_line(_set_line(p["lines"], "Width", f"{KIT_HALF_WIDTH:.1f}"), "SegmentMeshOffset", "0.0")
    for s in segments:
        if s["a"] in touched or s["b"] in touched:
            retangent(by_i, s)
    return {"road_segments": road_segs, "pieces": counts, "unfit": len(unfit), "unfit_nodes": unfit[:60]}


def pending_nodes(points: list, segments: list, rebuild: bool = False) -> list:
    """Old fitted junction nodes (3+ arms) still waiting for a Road1-width piece: [(node, [mouths], [angles])].
    rebuild: also the nodes that already have a generated SM_JK_ piece (to regenerate it in place)."""
    by_i = {p["i"]: p for p in points}
    links: dict = {}
    for s in segments:
        if not s["mesh"]:
            links.setdefault(s["a"], []).append(s["b"])
            links.setdefault(s["b"], []).append(s["a"])
    out = []
    for p in points:
        waiting = p["mesh"].startswith("SM_Junc_") and p["mesh"] != "SM_Junc_Empty"
        # 3+ arms: a junction; 2 arms: an elbow (a street's square corner)
        if (waiting or (rebuild and p["mesh"].startswith("SM_JK_"))) and len(links.get(p["i"], [])) >= 2:
            arms = links[p["i"]]
            out.append((p["i"], arms, [math.degrees(math.atan2(by_i[m]["y"] - p["y"], by_i[m]["x"] - p["x"])) for m in arms]))
    return out


def place_generated(points: list, segments: list, node: int, arms: list, dists: list, mesh_path: str) -> None:
    """Put a generated piece (built in world axes, yaw 0) on a node and its mouths on the piece's ends."""
    by_i = {p["i"]: p for p in points}
    p = by_i[node]
    name = mesh_path.rsplit("/", 1)[-1]
    p["mesh"], p["yaw"], p["pitch"] = name, 0.0, 0.0
    p["w"] = min(GIANT_CM - 1, max(dists) + 50.0)
    lines = [ln for ln in p["lines"] if not ln.startswith(("Mesh=", "MeshScale=", "MaterialOverrides="))]
    p["lines"] = _set_line(lines + [f"Mesh=\"/Script/Engine.StaticMesh'{mesh_path}.{name}'\"",
                                    "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"], "Width", f"{p['w']:.1f}")
    touched = {node}
    for m, d in zip(arms, dists):
        q = by_i[m]
        ang = math.atan2(q["y"] - p["y"], q["x"] - p["x"])
        q["x"], q["y"], q["z"] = p["x"] + d * math.cos(ang), p["y"] + d * math.sin(ang), p["z"]
        q["yaw"], q["pitch"] = math.degrees(ang), 0.0
        touched.add(m)
    for s in segments:
        if s["a"] in touched or s["b"] in touched:
            retangent(by_i, s)


def _ring_path(by_i: dict, adj: dict, start: int, goal: int, ring: set) -> list:
    """Shortest point path start -> goal that passes no other ring junction (BFS)."""
    prev, frontier = {start: None}, [start]
    while frontier:
        nxt = []
        for i in frontier:
            for n in adj.get(i, ()):
                if n in prev or (n in ring and n != goal) or (n != goal and by_i[n]["mesh"]):
                    continue  # never walk through another junction or kit piece
                prev[n] = i
                if n == goal:
                    path = [n]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                nxt.append(n)
        frontier = nxt
    return []


def circularize(points: list, segments: list, ring_nodes: list, center: tuple | None = None,
                radius: float = 0.0) -> dict:
    """Make a ring road a true circle. ring_nodes: the junction points on the ring (any order).
    Each junction (with its mouths) slides onto the circle, turned so its ring arms follow the
    circle; the road between two ring junctions becomes ONE arc segment, mouth to mouth, with
    circle tangents (4 R tan(angle/4)); the points it used to have go. Spokes stay attached."""
    by_i = {p["i"]: p for p in points}
    adj: dict = {}
    for s in segments:
        adj.setdefault(s["a"], set()).add(s["b"])
        adj.setdefault(s["b"], set()).add(s["a"])
    ring = [n for n in ring_nodes if n in by_i]
    if len(ring) < 3:
        return {"ok": False, "error": "Give at least 3 junctions on the ring."}
    if center:
        cx, cy = center
    else:  # least-squares circle through the ring junctions (Kasa fit)
        xs, ys = [by_i[n]["x"] for n in ring], [by_i[n]["y"] for n in ring]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        u, v = [x - mx for x in xs], [y - my for y in ys]
        suu, svv, suv = sum(a * a for a in u), sum(b * b for b in v), sum(a * b for a, b in zip(u, v))
        suuu = sum(a ** 3 for a in u) + sum(a * b * b for a, b in zip(u, v))
        svvv = sum(b ** 3 for b in v) + sum(a * a * b for a, b in zip(u, v))
        det = suu * svv - suv * suv or 1e-9
        uc, vc = (svv * suuu - suv * svvv) / (2 * det), (suu * svvv - suv * suuu) / (2 * det)
        cx, cy = mx + uc, my + vc
    R = radius or sum(math.hypot(by_i[n]["x"] - cx, by_i[n]["y"] - cy) for n in ring) / len(ring)
    ring.sort(key=lambda n: math.atan2(by_i[n]["y"] - cy, by_i[n]["x"] - cx))  # counter-clockwise
    ring_set = set(ring)
    paths = [_ring_path(by_i, adj, ring[k], ring[(k + 1) % len(ring)], ring_set) for k in range(len(ring))]
    if any(not p for p in paths):
        return {"ok": False, "error": "These junctions aren't joined around one ring by roads."}
    links = {n: {m for m in adj[n] if any(not s["mesh"] and {s["a"], s["b"]} == {n, m} for s in segments)}
             for n in ring}
    # 1. slide + turn every ring junction group
    for k, n in enumerate(ring):
        p = by_i[n]
        m_next, m_prev = paths[k][1], paths[k - 1][-2]  # this junction's mouths toward its neighbours
        axis = math.degrees(math.atan2(by_i[m_next]["y"] - by_i[m_prev]["y"], by_i[m_next]["x"] - by_i[m_prev]["x"]))
        phi = math.atan2(p["y"] - cy, p["x"] - cx)
        delta = _wrap(math.degrees(phi) + 90.0 - axis)
        tx, ty = cx + R * math.cos(phi), cy + R * math.sin(phi)
        c, s_ = math.cos(math.radians(delta)), math.sin(math.radians(delta))
        for g in {n} | links[n]:
            q = by_i[g]
            dx, dy = q["x"] - p["x"], q["y"] - p["y"]
            q["_nx"], q["_ny"] = tx + c * dx - s_ * dy, ty + s_ * dx + c * dy
            q["yaw"] = _wrap(q["yaw"] + delta)
    for q in points:
        if "_nx" in q:
            q["x"], q["y"] = q.pop("_nx"), q.pop("_ny")
    # 2. one arc segment per stretch, mouth to mouth
    drop_pts, drop_segs, arcs, new_arcs = set(), set(), 0, []
    for path in paths:
        ma, mb = path[1], path[-2]
        inner = set(path[2:-2])
        drop_pts |= inner
        stretch = [s for s in segments if s["a"] in set(path[1:-1]) and s["b"] in set(path[1:-1]) and s["mesh"]]
        if not stretch:
            continue
        keep = dict(stretch[0])
        drop_segs |= {s["i"] for s in stretch}
        keep["a"], keep["b"] = ma, mb
        pa, pb = by_i[ma], by_i[mb]
        span = abs(_wrap(math.degrees(math.atan2(pb["y"] - cy, pb["x"] - cx) - math.atan2(pa["y"] - cy, pa["x"] - cx))))
        tan_len = 4 * R * math.tan(math.radians(span) / 4)
        keep["ta"], keep["tb"] = tan_len, tan_len
        for key, p, q in (("ta", pa, pb), ("tb", pb, pa)):
            f = forward({"pitch": 0.0, "yaw": p["yaw"]})
            if f[0] * (q["x"] - p["x"]) + f[1] * (q["y"] - p["y"]) <= 0:
                keep[key] = -tan_len
        new_arcs.append(keep)
        arcs += 1
    segments[:] = [s for s in segments if s["i"] not in drop_segs] + new_arcs
    points[:] = [p for p in points if p["i"] not in drop_pts]
    # 3. re-aim every other segment that touches a moved point
    by_i = {p["i"]: p for p in points}
    moved = set(ring) | {m for n in ring for m in links[n]}
    arc_pairs = {(pp[1], pp[-2]) for pp in paths}
    for s in segments:
        if (s["a"], s["b"]) not in arc_pairs and (s["a"] in moved or s["b"] in moved):
            retangent(by_i, s)
    return {"ok": True, "center": (round(cx), round(cy)), "radius_cm": round(R), "junctions": len(ring),
            "arcs": arcs, "points_removed": len(drop_pts)}


def bad_tangents(points: list, segments: list) -> list:
    """Segments whose tangent is far longer than the segment (they loop into the sky)."""
    by_i = {p["i"]: p for p in points}
    bad = []
    for s in segments:
        a, b = by_i.get(s["a"]), by_i.get(s["b"])
        if not a or not b:
            continue
        chord = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
        if max(abs(s["ta"]), abs(s["tb"])) > 3 * chord + 2000:
            bad.append({"segment": s["i"], "a": s["a"], "b": s["b"], "chord_cm": round(chord),
                        "tangent_cm": round(max(abs(s["ta"]), abs(s["tb"])))})
    return bad


def stats(points: list, segments: list) -> dict:
    """Pieces used: segment meshes, point meshes (junctions, kit pieces), total length."""
    seg_mesh: dict = {}
    pt_mesh: dict = {}
    by_i = {p["i"]: p for p in points}
    length = 0.0
    for s in segments:
        a, b = by_i.get(s["a"]), by_i.get(s["b"])
        if a and b:
            d = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
            if s["mesh"]:
                length += d
                seg_mesh[s["mesh"]] = seg_mesh.get(s["mesh"], 0) + 1
    for p in points:
        if p["mesh"]:
            key = "SM_Junc_*" if p["mesh"].startswith("SM_Junc_") and p["mesh"] != "SM_Junc_Empty" else p["mesh"]
            pt_mesh[key] = pt_mesh.get(key, 0) + 1
    degree: dict = {}
    for s in segments:
        for k in (s["a"], s["b"]):
            degree[k] = degree.get(k, 0) + 1
    ends = sum(1 for p in points if degree.get(p["i"], 0) == 1 and not p["mesh"])
    return {"points": len(points), "segments": len(segments), "road_length_m": round(length / 100.0),
            "segment_meshes": seg_mesh, "point_meshes": pt_mesh,
            "live_terrain_points": sum(1 for p in points if p["raise"]), "open_ends": ends}


def _demo() -> None:
    """Self-check: a road A-B-C into junction J (mouth M, mesh-less link) and a bridge point D."""
    def pt(i, x, z, live=True, mesh=""):
        return {"i": i, "x": x, "y": 0.0, "z": z, "pitch": 0.0, "yaw": 0.0, "roll": 0.0,
                "w": 500.0, "side": 2500.0, "mesh": mesh, "raise": live, "lines": []}

    def seg(i, a, b, mesh="SM_Road"):
        return {"i": i, "a": a, "b": b, "ta": 1000.0, "tb": -1000.0, "mesh": mesh, "raise": True, "lines": []}

    pts = [pt(0, 0, 900), pt(1, 1000, 900), pt(2, 2000, 900), pt(3, 3000, 900),
           pt(4, 3500, 900, mesh="SM_Junc_0001"), pt(5, -1000, 2000, live=False)]
    segs = [seg(0, 0, 1), seg(1, 1, 2), seg(2, 2, 3), seg(3, 3, 4, mesh=""), seg(4, 5, 0)]
    ground = {0: 0.0, 1: 100.0, 2: 200.0, 3: 300.0, 4: 500.0}
    out = drape(pts, segs, ground, iters=0)
    z = {p["i"]: p["z"] for p in pts}
    assert z[0] == 900, "a point touching a bridge keeps its height"
    assert z[1] == 100 and z[2] == 200, "road points sit on the ground"
    assert z[3] == z[4] == 400, "a junction group is flat at its mean"
    assert z[5] == 2000, "non-live points never move"
    assert pts[1]["pitch"] < 0, "pitch follows the new slope (from 900 down to 200)"
    assert out["groups"] == 1 and out["kept_at_joints"] == 1
    # a link between two points 1 cm apart, then a 40 m drop: the tangent must not explode
    pts2 = [pt(0, 0, 4000), pt(1, 0.01, 4000), pt(2, 1000, 0)]
    segs2 = [seg(0, 0, 1, mesh=""), seg(1, 1, 2)]
    segs2[0]["ta"], segs2[0]["tb"] = 10.0, -10.0
    drape(pts2, segs2, {0: 0.0, 1: 0.0, 2: 0.0}, iters=0)
    assert not bad_tangents(pts2, segs2), "no tangent may grow far past its segment"
    assert abs(segs2[0]["ta"]) <= 10.0
    grid = {"min": [0, 0], "max": [200, 200], "grid": 2, "heights": [[0.0, 100.0], [None, 100.0]]}
    assert sample_grid(grid, 50, 50) == 0.0 and sample_grid(grid, 150, 50) == 100.0
    assert abs(sample_grid(grid, 100, 50) - 50.0) < 1e-6
    assert sample_grid(grid, 100, 150) is not None, "a missing cell falls back to its known neighbours"
    assert sample_grid({}, 0, 0) is None
    # a junction joined to a mouth 1 km away is giant: repair drops the link and the piece
    pts3 = [pt(0, 0, 0, mesh="SM_Junc_0514"), pt(1, 100000, 0), pt(2, 1500, 0)]
    pts3[0]["w"] = 106852.0
    pts3[0]["lines"] = ["Width=106852.0", "SideFalloff=2500.0", "Mesh=\"/Script/Engine.StaticMesh'/x/SM_Junc_0514.SM_Junc_0514'\"",
                        "MeshScale=(X=1,Y=1,Z=1)", "bRaiseTerrain=True"]
    segs3 = [seg(0, 0, 1, mesh=""), seg(1, 0, 2, mesh="")]
    assert problems(pts3, segs3)["giant_nodes"] and problems(pts3, segs3)["long_links"]
    fix = repair(pts3, segs3)
    assert fix["left"] == {"giant_nodes": 0, "long_links": 0, "bad_tangents": 0}, fix
    assert [s["i"] for s in segs3] == [1] and pts3[0]["mesh"] == "" and pts3[0]["w"] == 1400.0
    assert "Width=1400.0" in pts3[0]["lines"] and not any(l.startswith("Mesh=") for l in pts3[0]["lines"])
    # a straight flat road every 10 m between two junctions keeps only its two ends
    pts4 = [pt(0, 0, 0, mesh="SM_Junc_A")] + [pt(k, k * 1000.0, 0) for k in range(1, 7)] + [pt(7, 7000.0, 0, mesh="SM_Junc_B")]
    segs4 = [seg(k, k, k + 1) for k in range(7)]
    segs4[3]["a"], segs4[3]["b"], segs4[3]["ta"], segs4[3]["tb"] = 4, 3, -1000.0, 1000.0  # one segment drawn backwards
    out4 = thin(pts4, segs4)
    assert out4["points_removed"] == 6 and len(segs4) == 1, out4
    assert segs4[0]["a"] == 0 and segs4[0]["b"] == 7 and segs4[0]["ta"] == 7000.0 and segs4[0]["tb"] == -7000.0, segs4[0]
    # junction A -> 9 m lead-in -> lead-in -> junction B 3 m to the side: the spline's own end
    # tangents enter both junctions square, so the lead-ins go and the road is one segment
    pl = [pt(0, 0, 0, mesh="SM_Junc_A"), pt(1, 900, 0), pt(2, 9100, 0), pt(3, 10000, 0, mesh="SM_Junc_B")]
    pl[2]["y"] = pl[3]["y"] = 300.0
    sl = [seg(k, k, k + 1) for k in range(3)]
    outl = thin(pl, sl)
    assert outl["points_removed"] == 2 and len(sl) == 1, outl
    # lead-ins on a slope (flat junctions, the road climbs 4 m between them) still go, and so do
    # lead-ins where the road bends right after the junction; the bend's own point stays
    J1, J2 = pt(70, 0, 0, mesh="SM_Junc_A"), pt(75, 6000, 400, mesh="SM_Junc_B")
    m1, l1, l2, m2 = pt(71, 1000, 0), pt(72, 1900, 60), pt(73, 4100, 340), pt(74, 5000, 400)
    hill = [J1, m1, l1, l2, m2, J2]
    sh = [seg(80, 70, 71, mesh=""), seg(81, 71, 72), seg(82, 72, 73), seg(83, 73, 74), seg(84, 74, 75, mesh="")]
    thin(hill, sh)
    assert {p["i"] for p in hill} == {70, 71, 74, 75}, sorted(p["i"] for p in hill)
    assert all("_lead_in" not in p for p in hill)
    K1, K2 = pt(90, 0, 0, mesh="SM_Junc_A"), pt(96, 3000, 0, mesh="SM_Junc_B")
    k_m1, k_l1, k_bend, k_l2, k_m2 = pt(91, 1000, 0), pt(92, 1900, 0), pt(93, 2500, 0), pt(94, 2500, 0), pt(95, 2500, 0)
    k_bend["y"], k_l2["y"], k_m2["y"] = 1500.0, 3100.0, 4000.0
    k_l2["yaw"] = k_m2["yaw"] = 90.0
    k_bend["yaw"] = 45.0
    K2["x"], K2["y"] = 2500.0, 5000.0
    bend = [K1, k_m1, k_l1, k_bend, k_l2, k_m2, K2]
    sk = [seg(100, 90, 91, mesh=""), seg(101, 91, 92), seg(102, 92, 93), seg(103, 93, 94), seg(104, 94, 95),
          seg(105, 95, 96, mesh="")]
    thin(bend, sk)
    left = {p["i"] for p in bend}
    assert 92 not in left and 94 not in left and 93 in left, sorted(left)
    # an egg-shaped ring of 4 junctions becomes a circle: junctions on it, one arc per stretch
    def junction(i, x, y):
        q = pt(i, x, 0, mesh="SM_Junc_R")
        q["y"] = y
        return q
    rp, rs, nid = [], [], 200
    nodes = [(0, -10000), (12000, 0), (0, 16000), (-10000, 0)]  # bulges on top and right
    for k, (x, y) in enumerate(nodes):
        rp.append(junction(k, x, y))
    for k in range(4):
        a, b = rp[k], rp[(k + 1) % 4]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        L = math.hypot(dx, dy)
        ma = pt(10 + k * 3, a["x"] + dx / L * 1500, 0)
        mid = pt(11 + k * 3, (a["x"] + b["x"]) / 2, 0)
        mb = pt(12 + k * 3, b["x"] - dx / L * 1500, 0)
        ma["y"], mid["y"], mb["y"] = a["y"] + dy / L * 1500, (a["y"] + b["y"]) / 2, b["y"] - dy / L * 1500
        for q in (ma, mid, mb):
            q["yaw"] = math.degrees(math.atan2(dy, dx))
        rp += [ma, mid, mb]
        rs += [seg(nid, a["i"], ma["i"], mesh=""), seg(nid + 1, ma["i"], mid["i"]), seg(nid + 2, mid["i"], mb["i"]),
               seg(nid + 3, mb["i"], b["i"], mesh="")]
        nid += 4
    outc = circularize(rp, rs, [0, 1, 2, 3])
    assert outc["ok"] and outc["arcs"] == 4 and outc["points_removed"] == 4, outc
    cxy, R = outc["center"], outc["radius_cm"]
    for k in range(4):
        q = next(p for p in rp if p["i"] == k)
        assert abs(math.hypot(q["x"] - cxy[0], q["y"] - cxy[1]) - R) < 2, (k, q["x"], q["y"])
    assert len([s for s in rs if s["mesh"]]) == 4 and not bad_tangents(rp, rs)
    # a bend keeps its corner point
    pts5 = [pt(0, 0, 0, mesh="SM_Junc_A"), pt(1, 1000, 0), pt(2, 2000, 0), pt(3, 3000, 0), pt(4, 4000, 0, mesh="SM_Junc_B")]
    pts5[2]["y"] = 800.0
    segs5 = [seg(k, k, k + 1) for k in range(4)]
    thin(pts5, segs5)
    assert 2 in {p["i"] for p in pts5}, "the bend stays"
    t = tune(pts5, segs5, width=600.0)
    assert t["points_tuned"] == len(pts5) and "SideFalloff=800.000000" in pts5[1]["lines"]
    assert pts5[0]["w"] == 500.0 and pts5[1]["w"] == 600.0, "junction pieces keep their width"
    # a T node (arms east, west, north) becomes Road1_T_Section with its mouths on the kit ends
    J = pt(10, 0, 500, mesh="SM_Junc_0001")
    M = [pt(11, 1500, 500), pt(12, -1500, 500), pt(13, 0, 500)]
    M[2]["y"] = 1500.0
    R = [pt(14, 5000, 500), pt(15, -5000, 500), pt(16, 0, 500)]
    R[2]["y"] = 5000.0
    segs6 = [seg(20, 10, 11, mesh=""), seg(21, 10, 12, mesh=""), seg(22, 10, 13, mesh=""),
             seg(23, 11, 14, mesh="SM_Road_TwoLane_Straight10m"), seg(24, 12, 15, mesh="SM_Road_TwoLane_Straight10m"),
             seg(25, 13, 16, mesh="SM_Road_TwoLane_Straight10m")]
    pts6 = [J] + M + R
    out6 = kit_convert(pts6, segs6)
    assert out6["pieces"]["Road1_T_Section"] == 1 and out6["unfit"] == 0, out6
    assert all(abs(math.hypot(m["x"], m["y"]) - 595.0) < 1 for m in M), [(m["x"], m["y"]) for m in M]
    assert all(s["mesh"] == KIT_ROAD for s in segs6 if s["i"] >= 23)
    assert not bad_tangents(pts6, segs6) and J["mesh"] == "Road1_T_Section"
    reparsed = parse(serialize(pts6, segs6))
    assert {p["mesh"] for p in reparsed["points"]} >= {"Road1_T_Section"} and reparsed["segments"][3]["mesh"] == KIT_ROAD
    assert fit_3way([180.0, 23.0, -23.0])[0] == "Road1_Y_Split"
    assert fit_3way([0.0, 10.0, 20.0])[0] is None, "three arms bunched together fit no kit piece"
    # a 2-arm corner piece becomes a plain point the road runs through
    C = pt(30, 0, 0, mesh="SM_Junc_0002")
    ca, cb = pt(31, -800, 0), pt(32, 0, 0)
    cb["y"] = 800.0
    segs7 = [seg(40, 30, 31, mesh=""), seg(41, 30, 32, mesh="")]
    out7 = kit_convert([C, ca, cb], segs7)
    assert out7["pieces"].get("through") == 1 and C["mesh"] == "" and all(s["mesh"] == KIT_ROAD for s in segs7)
    assert not bad_tangents([C, ca, cb], segs7)
    # a 4-way node waits for a generated piece; placing it puts the mouths on the piece ends
    X = pt(50, 0, 300, mesh="SM_Junc_0003")
    xm = [pt(51, 2000, 300), pt(52, -2000, 300), pt(53, 0, 300), pt(54, 0, 300)]
    xm[2]["y"], xm[3]["y"] = 2000.0, -2000.0
    segs8 = [seg(60 + k, 50, 51 + k, mesh="") for k in range(4)]
    todo = pending_nodes([X] + xm, segs8)
    assert len(todo) == 1 and len(todo[0][1]) == 4
    place_generated([X] + xm, segs8, 50, todo[0][1], [845.0] * 4, "/Game/x/Kit/SM_JK_50")
    assert X["mesh"] == "SM_JK_50" and all(abs(math.hypot(q["x"], q["y"]) - 845.0) < 1 for q in xm)
    assert not pending_nodes([X] + xm, segs8) and not bad_tangents([X] + xm, segs8)
    print("drape ok", out)


if __name__ == "__main__":
    _demo()

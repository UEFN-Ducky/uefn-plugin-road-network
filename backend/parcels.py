"""City blocks and lots from the street graph, the way real cities are subdivided.

Roads define the city, blocks are what the roads enclose, lots are cut from blocks, buildings fit lots
(Vanegas et al. 2012, "Procedural Generation of Parcels in Urban Modeling": OBB subdivision).
1. blocks(): the faces of the planar street graph (each a polygon of intersection points).
2. inset(): pull each block edge in by its road's half width + the sidewalk (the buildable area).
3. split(): recursively cut a block across the long side of its oriented box until a lot is the district's
   size; only lots that touch the block edge (the street) are kept, so every lot has a frontage.
4. lots(): each lot's centre, facing (+X toward the street it fronts), width along the street and depth.
"""

from __future__ import annotations

import math

SIDEWALK_CM = 380.0  # pedestrian zone between kerb and lot line (1.8 m clear path + frontage + furniture strip)


def blocks(nodes: dict, edges: list) -> list:
    """Every face of the planar street graph except the outer one: [[node key, ...], ...] counter-clockwise."""
    nbrs: dict = {}
    for a, b, *_ in edges:
        if a != b:
            nbrs.setdefault(a, set()).add(b)
            nbrs.setdefault(b, set()).add(a)
    order = {n: sorted(v, key=lambda m: math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0]))
             for n, v in nbrs.items()}
    seen, faces = set(), []
    for a in order:
        for b in order[a]:
            if (a, b) in seen:
                continue
            face, u, v = [], a, b
            while (u, v) not in seen and len(face) < 64:
                seen.add((u, v))
                face.append(u)
                around = order[v]
                k = around.index(u)
                u, v = v, around[(k - 1) % len(around)]  # next edge: turn as far right as possible
            if len(face) >= 3 and _area([nodes[n] for n in face]) > 0:
                faces.append(face)
    return faces


def _area(poly: list) -> float:
    return 0.5 * sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))


def inset(poly: list, by: list) -> list:
    """Each edge i moved inward by by[i] cm (counter-clockwise polygon); corners where the moved edges meet."""
    lines = []
    for i, (p, q) in enumerate(zip(poly, poly[1:] + poly[:1])):
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L  # inward for a counter-clockwise polygon
        lines.append(((p[0] + nx * by[i], p[1] + ny * by[i]), (dx, dy)))
    out = []
    for (p1, d1), (p2, d2) in zip(lines[-1:] + lines[:-1], lines):
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-9:
            out.append(p2)
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
        out.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t))
    return out if _area(out) > 0 else []


def _clip(poly: list, px: float, py: float, nx: float, ny: float) -> list:
    """The part of poly on the side (x - p) . n >= 0 (Sutherland-Hodgman, one plane)."""
    out = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        dp, dq = (p[0] - px) * nx + (p[1] - py) * ny, (q[0] - px) * nx + (q[1] - py) * ny
        if dp >= 0:
            out.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp / (dp - dq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def _obb(poly: list) -> tuple:
    """Oriented box from the longest edge: (cx, cy, ux, uy, length along u, width across)."""
    p, q = max(zip(poly, poly[1:] + poly[:1]), key=lambda e: math.dist(e[0], e[1]))
    L = math.dist(p, q) or 1.0
    ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
    us = [v[0] * ux + v[1] * uy for v in poly]
    vs = [-v[0] * uy + v[1] * ux for v in poly]
    cu, cv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
    return cu * ux - cv * uy, cu * uy + cv * ux, ux, uy, max(us) - min(us), max(vs) - min(vs)


def split(poly: list, lot_w: float, lot_d: float, rnd, out: list | None = None) -> list:
    """How plats are drawn: a block deeper than one lot row is first cut down its length into back-to-back
    strips (each about half the block deep, both fronting a street); each strip is then cut ACROSS its own
    axis into lots about lot_w wide (a little uneven, like real plats). The strip's axis is kept for all its
    cuts, so lots stay frontage x depth instead of being whittled into squares."""
    out = [] if out is None else out
    cx, cy, ux, uy, length, width = _obb(poly)
    if width > lot_d * 1.4:  # too deep for one row: back-to-back strips
        for side in (1, -1):
            part = _clip(poly, cx, cy, -uy * side, ux * side)
            if len(part) >= 3 and abs(_area(part)) > 1e4:
                split(part, lot_w, lot_d, rnd, out)
        return out
    n = max(1, round(length / lot_w))
    rest = poly
    for k in range(1, n):
        t = -length / 2 + length * k / n + (rnd.random() - 0.5) * 0.2 * length / n
        px, py = cx + ux * t, cy + uy * t
        piece = _clip(rest, px, py, -ux, -uy)
        rest = _clip(rest, px, py, ux, uy)
        if len(piece) >= 3 and abs(_area(piece)) > 1e4:
            out.append(piece)
    if len(rest) >= 3 and abs(_area(rest)) > 1e4:
        out.append(rest)
    return out


def lots(nodes: dict, edges: list, half: dict, lot_w_cm: float, lot_d_cm: float, rnd) -> list:
    """[[x, y, yaw, width_cm, depth_cm, [polygon], front x, front y]] - every lot fronting a street (centre,
    +X toward the street, frontage, depth, outline, middle of its front lot line). half: road kind -> half width."""
    kind = {}
    for a, b, k, *_ in edges:
        kind[(a, b)] = kind[(b, a)] = k
    out = []
    for face in blocks(nodes, edges):
        poly = [tuple(nodes[n][:2]) for n in face]
        by = [half.get(kind.get((face[i], face[(i + 1) % len(face)])), 500.0) + SIDEWALK_CM for i in range(len(face))]
        area = inset(poly, by)
        if not area:
            continue
        edges_out = list(zip(area, area[1:] + area[:1]))
        for piece in split(area, lot_w_cm, lot_d_cm, rnd):
            # its frontage: a stretch of the piece lying on the block's edge (the street side); a corner lot
            # touches two streets and fronts the one that leaves it deepest (its short side, like real corners)
            fronts = []
            for p, q in zip(piece, piece[1:] + piece[:1]):
                for a, b in edges_out:
                    ex, ey = b[0] - a[0], b[1] - a[1]
                    L = math.hypot(ex, ey) or 1.0
                    off = lambda v: abs((v[0] - a[0]) * ey - (v[1] - a[1]) * ex) / L
                    if off(p) < 5 and off(q) < 5 and math.dist(p, q) >= lot_w_cm * 0.4:
                        f = math.dist(p, q)
                        ox, oy = (q[1] - p[1]) / f, -(q[0] - p[0]) / f  # outward for a counter-clockwise block
                        fronts.append((max((v[0] - p[0]) * -ox + (v[1] - p[1]) * -oy for v in piece), f, p, q, ox, oy))
            if not fronts:
                continue  # an inner piece with no street: a back garden, not a lot
            depth, best, p, q, ox, oy = max(fronts, key=lambda c: c[0])
            if depth < lot_d_cm * 0.3:
                continue  # a sliver left by an off-centre cut: open space, not a lot
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            out.append([round(mx - ox * depth / 2), round(my - oy * depth / 2), round(math.degrees(math.atan2(oy, ox)), 1),
                        round(best), round(depth), [[round(v[0]), round(v[1])] for v in piece], round(mx), round(my)])
    return out


def _demo() -> None:
    import random
    # a 3 x 3 grid of 100 m blocks with 2-lane streets (5.25 m half width)
    nodes = {(i, j): [i * 10000.0, j * 10000.0] for i in range(4) for j in range(4)}
    edges = [[(i, j), (i + 1, j), "2L"] for i in range(3) for j in range(4)] + \
            [[(i, j), (i, j + 1), "2L"] for i in range(4) for j in range(3)]
    assert len(blocks(nodes, edges)) == 9, "nine blocks"
    got = lots(nodes, edges, {"2L": 525.0}, 1500, 3000, random.Random(1))
    assert len(got) >= 9 * 8, len(got)
    for x, y, yaw, w, d, poly, frx, fry in got:
        bi, bj = int(x // 10000), int(y // 10000)
        lo = 525 + SIDEWALK_CM - 2
        assert bi * 10000 + lo <= x <= (bi + 1) * 10000 - lo and bj * 10000 + lo <= y <= (bj + 1) * 10000 - lo, (x, y)
        assert yaw % 90 == 0, yaw  # faces a street square on
        # +X (yaw) points out of the block toward its nearest street
        fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        cx, cy = (bi + 0.5) * 10000, (bj + 0.5) * 10000
        assert (x - cx) * fx + (y - cy) * fy > 0, "faces outward"
        assert 700 <= w <= 2600 and 900 <= d <= 7000, (w, d)
    print("parcels ok", len(got), "lots in 9 blocks")


if __name__ == "__main__":
    _demo()

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
    """Every face of the planar street graph except the outer one: [[node key, ...], ...] counter-clockwise.
    Each face is walked whole, however many corners it has. The walk once stopped at 64: the city's outer edge
    (hundreds of corners) came back as stretches of 64, and every stretch that happened to turn the right way
    became a "block" closed straight across the streets and blocks inside it. A subdivision was then laid out
    over streets that were already there (Nov 12 2026)."""
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
            while (u, v) not in seen:
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
    """Each edge i moved inward by by[i] cm (counter-clockwise polygon); corners where the moved edges meet.
    Vertices where the outline runs (nearly) straight on are dropped first: two near-parallel edges meet
    absurdly far away once moved, and blew a block up to the size of the map."""
    keep = []
    for i, v in enumerate(poly):
        a, c = poly[i - 1], poly[(i + 1) % len(poly)]
        d1, d2 = (v[0] - a[0], v[1] - a[1]), (c[0] - v[0], c[1] - v[1])
        l1, l2 = math.hypot(*d1) or 1.0, math.hypot(*d2) or 1.0
        cross = (d1[0] * d2[1] - d1[1] * d2[0]) / (l1 * l2)
        dot = (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)
        if abs(cross) > 0.14 or dot < 0:  # turns more than ~8 degrees: a real corner
            keep.append(i)
    if len(keep) < 3:
        return []
    merged = []
    for n, k in enumerate(keep):  # a merged edge runs from corner k over the dropped vertices to the next corner
        nxt, j, inset_by = keep[(n + 1) % len(keep)], k, by[k]
        while j != nxt:
            inset_by = max(inset_by, by[j])
            j = (j + 1) % len(poly)
        merged.append(inset_by)
    by = merged
    size = max(max(p[0] for p in poly) - min(p[0] for p in poly), max(p[1] for p in poly) - min(p[1] for p in poly))
    cx0, cy0 = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    poly = [poly[k] for k in keep]
    lines = []
    for i, (p, q) in enumerate(zip(poly, poly[1:] + poly[:1])):
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L  # inward for a counter-clockwise polygon
        lines.append(((p[0] + nx * by[i], p[1] + ny * by[i]), (dx, dy)))
    out = []
    for i, ((p1, d1), (p2, d2)) in enumerate(zip(lines[-1:] + lines[:-1], lines)):
        den = d1[0] * d2[1] - d1[1] * d2[0]
        l1, l2 = math.hypot(*d1) or 1.0, math.hypot(*d2) or 1.0
        if abs(den) < 0.02 * l1 * l2 and d1[0] * d2[0] + d1[1] * d2[1] < 0:
            # the end of a dead-end street inside the block (the outline runs out along it and straight back):
            # round its end, square, as far off as off its sides
            b = max(by[i - 1], by[i])
            ux, uy = d1[0] / l1, d1[1] / l1
            out.append((poly[i][0] - uy * by[i - 1] + ux * b, poly[i][1] + ux * by[i - 1] + uy * b))
            out.append((poly[i][0] + uy * by[i] + ux * b, poly[i][1] - ux * by[i] + uy * b))
            continue
        if abs(den) < 1e-9:
            out.append(p2)
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
        out.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t))
    if any(math.hypot(p[0] - cx0, p[1] - cy0) > size for p in out):
        return []  # a corner ran away (very sharp or concave outline): leave this block empty, never the map
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


def block_faces(nodes: dict, edges: list, half: dict) -> list:
    """Every block as (its buildable area, the streets round it [(node, next node, kind)] in the same order):
    its polygon inset by each street's half width + the sidewalk zone."""
    kind = {}
    for a, b, k, *_ in edges:
        kind[(a, b)] = kind[(b, a)] = k
    out = []
    for face in blocks(nodes, edges):
        poly = [tuple(nodes[n][:2]) for n in face]
        sides = [(face[i], face[(i + 1) % len(face)], kind.get((face[i], face[(i + 1) % len(face)]))) for i in range(len(face))]
        area = inset(poly, [half.get(k, 500.0) + SIDEWALK_CM for _a, _b, k in sides])
        if area:
            out.append((area, sides))
    return out


def block_areas(nodes: dict, edges: list, half: dict) -> list:
    """Every block's buildable area: its polygon inset by each street's half width + the sidewalk zone."""
    return [area for area, _sides in block_faces(nodes, edges, half)]


def solid(area: list) -> float:
    """How much of its own oriented box a block fills, 0..1: 1 for a rectangle, about 0.75 for an L. A layout
    drawn in the block's box (a subdivision, an apartment complex) fits a block that fills its box."""
    _, _, _, _, length, width = _obb(area)
    return abs(_area(area)) / max(1.0, length * width)


def lots_in(area: list, lot_w_cm: float, lot_d_cm: float, rnd) -> list:
    """The street-fronting lots cut from one block area (see lots())."""
    out = []
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


def lots(nodes: dict, edges: list, half: dict, lot_w_cm: float, lot_d_cm: float, rnd) -> list:
    """[[x, y, yaw, width_cm, depth_cm, [polygon], front x, front y]] - every lot fronting a street (centre,
    +X toward the street, frontage, depth, outline, middle of its front lot line). half: road kind -> half width."""
    out = []
    for area in block_areas(nodes, edges, half):
        out += lots_in(area, lot_w_cm, lot_d_cm, rnd)
    return out


def _demo() -> None:
    import random
    # a 3 x 3 grid of 100 m blocks with 2-lane streets (5.25 m half width)
    nodes = {(i, j): [i * 10000.0, j * 10000.0] for i in range(4) for j in range(4)}
    edges = [[(i, j), (i + 1, j), "2L"] for i in range(3) for j in range(4)] + \
            [[(i, j), (i, j + 1), "2L"] for i in range(4) for j in range(3)]
    assert len(blocks(nodes, edges)) == 9, "nine blocks"
    # the outer edge is never a block, however long: a 20 x 20 grid's outline has 80 corners (it was once cut
    # into stretches of 64, and a stretch round a bay closed into a block over the streets inside it)
    wide = {(i, j): [i * 10000.0, j * 10000.0] for i in range(21) for j in range(21) if not (8 <= i <= 12 and j < 6)}
    wide_e = [[a, b, "2L"] for a in wide for b in ((a[0] + 1, a[1]), (a[0], a[1] + 1)) if b in wide]
    faces = blocks(wide, wide_e)
    assert len(faces) == 400 - 6 * 6, len(faces)
    assert max(len(f) for f in faces) == 4, "every block of a grid is a square, round a bay too"
    # a dead-end street inside a block: the block's area goes round it, on both sides and past its end
    stub = {**{(i, j): [i * 20000.0, j * 20000.0] for i in range(2) for j in range(2)}, "m": [10000.0, 0.0], "t": [10000.0, 9000.0]}
    stub_e = [[(0, 0), "m", "2L"], ["m", (1, 0), "2L"], [(1, 0), (1, 1), "2L"], [(1, 1), (0, 1), "2L"], [(0, 1), (0, 0), "2L"],
              ["m", "t", "2L"]]
    (area, sides), = block_faces(stub, stub_e, {"2L": 525.0})
    off = 525 + SIDEWALK_CM
    assert len(sides) == 7 and abs(_area(area) - ((20000 - 2 * off) ** 2 - 2 * off * (9000 + off - off))) < 1e3, _area(area)
    assert all(not (10000 - off + 1 < x < 10000 + off - 1 and y < 9000 + off - 1) for x, y in area), "nothing of it on the street"
    assert 0.9 < solid(area) < 1.0 and abs(solid([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]) - 0.75) < 1e-9
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

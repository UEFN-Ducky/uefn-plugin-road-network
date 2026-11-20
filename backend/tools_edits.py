"""The person's own edits, road and ground fit, loose ends, exact PCG, kit materials and the street kit.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import math
import os

from . import citygen as cg
from . import kit_bake
from . import pcg_layers as pl
from . import road_profiles as rp
from . import splines_text as st
from . import uefn_memory


def register(ctx) -> None:
    (_GROUND, _PREVIEW_CACHE, actor_docs, data, fit_summary, interiors_of, listener, listener_long,
     load_actor, pcg_preview, road_folder, scan_terrain, slug, tree_props) = ctx.take(
        "_GROUND _PREVIEW_CACHE actor_docs data fit_summary interiors_of listener listener_long load_actor "
        "pcg_preview road_folder scan_terrain slug tree_props")

    # ---------------------------------------------------------------- the person's own edits
    def edit_list(nid: str) -> list:
        return list((data.get(f"net.{nid}.edits") or {}).get("items") or [])

    def camera_point() -> tuple:
        """Where the middle of the UEFN viewport looks: the camera's ray down to the city's ground (cm)."""
        cam = listener("get_viewport_camera", {}, timeout=20)
        loc, rot = cam["location"], cam["rotation"]
        p, w = math.radians(rot["pitch"]), math.radians(rot["yaw"])
        d = (math.cos(p) * math.cos(w), math.cos(p) * math.sin(w), math.sin(p))
        if d[2] > -0.05:
            raise ValueError("The UEFN camera looks at the sky: look down at the piece (the middle of the view).")
        g = _PREVIEW_CACHE.get("ground") or (lambda x, y: 0.0)
        x, y, z = loc["x"], loc["y"], loc["z"]
        for _ in range(2000):  # ponytail: 1 m steps, 2 km reach
            if z <= g(x, y):
                break
            x, y, z = x + d[0] * 100, y + d[1] * 100, z + d[2] * 100
        return x, y

    def tile_of(x: float, y: float, name: str) -> str:
        lay = (_PREVIEW_CACHE.get("out") or {}).get(name) or {}
        n = lay.get("kind") == "foliage" or lay.get("role") in pl.NATURAL
        return f"n{math.floor(x / 160000)}_{math.floor(y / 160000)}" if n else f"{math.floor(x / 40000)}_{math.floor(y / 40000)}"

    def edit(net_id: str = "", op: str = "delete", x=None, y=None, layer: str = "", to_x=None, to_y=None,
             yaw=None, at: str = "", to: str = "", reach_m: float = 0.0, every: bool = False) -> dict:
        """Delete or move one placed piece for good: the edit is kept with the world and applied after every rule,
        so each rebuild keeps it. at='camera' / to='camera': the point the middle of the UEFN view looks at."""
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        if op not in ("delete", "move", "copy"):
            return {"ok": False, "error": "op is 'delete', 'move' or 'copy'."}
        reach = float(reach_m) * 100 if reach_m else pl.EDIT_REACH_CM
        pcg_preview(nid, summary=True)  # (the placement as it stands, with the edits so far)
        if at == "camera":
            x, y = camera_point()
        if x is None or y is None:
            return {"ok": False, "error": "Give x and y (cm), or at='camera' to take what the UEFN view looks at."}
        name, p = pl.nearest(_PREVIEW_CACHE["out"], float(x), float(y), layer, reach)
        if p is None:
            return {"ok": False, "error": f"Nothing{' of ' + layer if layer else ''} stands within "
                                           f"{round(reach / 100)} m of {round(float(x))}, {round(float(y))}."}
        if every and op == "delete":  # everything within reach (of the layer, if one is named): kept at its point
            hits = {n: sum(math.hypot(q[0] - float(x), q[1] - float(y)) <= reach for q in v.get("points") or [])
                    for n, v in _PREVIEW_CACHE["out"].items() if not layer or n == layer}
            e = {"op": op, "layer": layer, "x": round(float(x)), "y": round(float(y)), "reach": reach, "every": True}
            p, name = [e["x"], e["y"], 0, 0], layer or "(every layer)"
        else:
            e = {"op": op, "layer": name, "x": p[0], "y": p[1]}
            hits = {name: 1}
        tiles = [tile_of(p[0], p[1], name if name in _PREVIEW_CACHE["out"] else "")]
        if op in ("move", "copy"):
            if to == "camera":
                to_x, to_y = camera_point()
            if to_x is None or to_y is None:
                return {"ok": False, "error": f"A {op} needs to_x and to_y (cm), or to='camera'."}
            e.update(to=[round(float(to_x)), round(float(to_y))], yaw=None if yaw is None else float(yaw))
            tiles = (tiles if op == "move" else []) + [tile_of(e["to"][0], e["to"][1], name)]  # (a copy's source stays)
        items = edit_list(nid) + [e]
        data.put(f"net.{nid}.edits", {"items": items})
        tiles = list(dict.fromkeys(tiles))
        return {"ok": True, "id": nid, "edit": e, "piece": {"layer": name, "at": p[:3], "yaw": p[3]},
                "pieces": {k: v for k, v in hits.items() if v}, "tiles": tiles, "edits": len(items),
                "next": f"Push {', '.join(tiles)}: road_network_pcg_build(net_id={nid!r}, tiles={', '.join(tiles)!r}, "
                        f"force=true, confirm=true)"}

    def edits(net_id: str = "", undo: bool = False, clear: bool = False) -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        items = edit_list(nid)
        dropped = items if clear else items[-1:] if undo else []
        if dropped:
            items = items[:len(items) - len(dropped)]
            data.put(f"net.{nid}.edits", {"items": items})
        tiles = sorted({tile_of(e["x"], e["y"], e["layer"]) for e in dropped}
                       | {tile_of(*e["to"], e["layer"]) for e in dropped if e.get("to")})
        if dropped:
            pcg_preview(nid, summary=True)
        return {"ok": True, "id": nid, "items": items, "count": len(items), "dropped": len(dropped),
                "tiles_to_push": tiles, "missed": _PREVIEW_CACHE.get("edits_missed") or []}

    def road_fit(ground, actors: list, terrain: dict) -> dict:
        """Every street point against the ground the buildings were fitted to: how many the ground covers (over)
        or leaves in the air (under) by more than 30 cm, and why. Bridges, viaducts, decks 20 m up and points
        over water stand free. A point off beside the water or beside a road at another height is a place the
        landscape cannot follow both (a wall or a bridge stands there); `unexplained` are the real misses."""
        n = over = under = 0
        worst = 0.0
        sea, raw = terrain.get("sea"), st.grid_sampler(terrain)
        bad: dict = {}
        worst_rows: list = []
        every: list = []  # every meshed segment, structures too: (ax, ay, az, bx, by, bz)
        grades: list = []  # every street segment's slope: a street steeper than 15 % is a kink the ground can't follow
        decks, deck_rows = [0, 0], []  # samples along decks and ramps, and those the ground reaches
        checks: list = []
        for pts, segs in actors:
            by = {p["i"]: p for p in pts}
            ids: dict = {}
            for s in segs:
                mesh = (s.get("mesh") or "").split("/")[-1]
                a, b = by.get(s["a"]), by.get(s["b"])
                if not mesh or not a or not b:
                    continue
                every.append((a["x"], a["y"], a["z"], b["x"], b["y"], b["z"]))
                if mesh in cg.STRUCTURES:  # a deck or ramp: checked along its length, the ground must stay under it
                    for k in range(5):
                        t = k / 4
                        x, y, z = a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t, a["z"] + (b["z"] - a["z"]) * t
                        decks[0] += 1
                        if ground(x, y) > z - 50 and not (sea is not None and (raw(x, y) or 0.0) < sea):
                            decks[1] += 1
                            deck_rows.append((round(ground(x, y) - z), round(x), round(y), round(z), mesh))
                if mesh not in cg.STRUCTURES and "Water_" not in mesh:
                    run = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                    if run > 300:
                        grades.append((abs(b["z"] - a["z"]) / run, round(a["x"]), round(a["y"]), round(run), mesh))
                    ids.setdefault(s["a"], mesh)
                    ids.setdefault(s["b"], mesh)
            checks += [(by[i], mesh) for i, mesh in ids.items()]
        ring = [(math.cos(k * math.pi / 4) * r, math.sin(k * math.pi / 4) * r) for r in (1500.0, 3000.0) for k in range(8)]

        def why(x, y, z):
            if sea is not None and any((raw(x + dx, y + dy) or 0.0) < sea for dx, dy in ring):
                return "at the water's edge"
            for ax, ay, az, bx, by_, bz in every:
                ux, uy = bx - ax, by_ - ay
                t = max(0.0, min(1.0, ((x - ax) * ux + (y - ay) * uy) / (ux * ux + uy * uy or 1.0)))
                if math.hypot(x - ax - ux * t, y - ay - uy * t) < 4000 and abs(az + (bz - az) * t - z) > 150:
                    return "beside or across a road at another height"
            return "unexplained"
        reasons: dict = {}
        high: list = []  # a plain road (not a deck or bridge) more than 20 m over dry ground
        for p, mesh in checks:
            gap = ground(p["x"], p["y"]) - p["z"] + cg.ROAD_SINK_CM  # measured from where the ground should be
            if sea is not None and (raw(p["x"], p["y"]) or 0.0) < sea:
                continue
            if gap < -2000:
                high.append((round(gap), round(p["x"]), round(p["y"]), round(p["z"]), mesh))
                continue
            n += 1
            over += gap > 30
            under += gap < -30
            worst = max(worst, abs(gap))
            if abs(gap) > 30:
                bad[mesh] = bad.get(mesh, 0) + 1
                r = why(p["x"], p["y"], p["z"])
                reasons[r] = reasons.get(r, 0) + 1
                worst_rows.append((round(gap), round(p["x"]), round(p["y"]), round(p["z"]), mesh, r))
        worst_rows.sort(key=lambda r: -abs(r[0]))
        loose = [r for r in worst_rows if r[5] == "unexplained"]
        # a junction's mesh is flat at its node: every arm must arrive at that height, or the road steps at its mouth
        steps = []
        for pts, segs in actors:
            by = {p["i"]: p for p in pts}
            for node, arms, _a in st.pending_nodes(pts, segs, True):
                if node in by:
                    q = by[node]
                    m = max((by[m] for m in arms if m in by), key=lambda r: abs(r["z"] - q["z"]), default=q)
                    steps.append((round(abs(m["z"] - q["z"])), round(q["x"]), round(q["y"]), round(q["z"]),
                                  round(m["x"]), round(m["y"]), round(m["z"])))
        junction_steps = {"checked": len(steps), "stepped": sum(1 for s in steps if s[0] > 10),
                          "over_50cm": sum(1 for s in steps if s[0] > 50),
                          "worst_cm": max([s[0] for s in steps] or [0]), "worst": sorted(steps, reverse=True)[:6],
                          "row": "step cm, node x, y, z, steepest arm x, y, z"}
        return {"checked": n, "ground_over_road": over, "road_in_air": under, "worst_cm": round(worst), "by_mesh": bad,
                 "high_over_land": {"count": len(high), "worst": sorted(high)[:6], "row": "ground minus road cm, x, y, z, mesh"},
                "why": reasons, "unexplained": len(loose), "junction_steps": junction_steps,
                "decks": {"samples": decks[0], "ground_through_deck": decks[1],
                          "worst": sorted(deck_rows, reverse=True)[:6], "row": "ground minus deck cm, x, y, deck z, mesh"},
                "steep": {"segments": len(grades), "over_15pct": sum(1 for g in grades if g[0] > 0.15),
                          "over_30pct": sum(1 for g in grades if g[0] > 0.30),
                          "worst": [(round(g[0] * 100), *g[1:]) for g in sorted(grades, reverse=True)[:6]],
                          "row": "grade %, x, y, run cm, mesh"},
                "unexplained_over": sum(1 for r in loose if r[0] > 0), "unexplained_worst_cm": abs(loose[0][0]) if loose else 0,
                "over_1m": sum(1 for r in worst_rows if r[0] > 100), "in_air_1m": sum(1 for r in worst_rows if r[0] < -100),
                "worst_unexplained": loose[:10],
                "worst_over": [r for r in worst_rows if r[0] > 0][:6], "worst_in_air": [r for r in worst_rows if r[0] < 0][:6],
                "row": "ground minus road cm, x, y, road z, mesh, why"}

    def loose_ends(nid: str, terrain: dict) -> dict:
        """Every open end of a freeway, ramp or highway as it was BUILT (the spline points, not the plan): it must
        land on another road, at that road's height. far = no road within 26 m; stepped = a road is there but more
        than 1 m above or below it. An end at the map's edge is the road leaving the world."""
        roads = []
        for aslug, head in actor_docs(nid).items():
            label = str(head.get("label") or "")
            if " before kit" in label or label.endswith((" Paths", " Access")):
                continue
            _, pts, segs = load_actor(nid, aslug)
            roads.append((label, {p["i"]: p for p in pts}, segs))
        every = [(label, by[s["a"]], by[s["b"]]) for label, by, segs in roads for s in segs
                 if s.get("mesh") and s["a"] in by and s["b"] in by]
        (x0, y0), (x1, y1) = terrain.get("min") or (-1e12, -1e12), terrain.get("max") or (1e12, 1e12)
        ends = edge = 0
        bad = []
        for label, by, segs in roads:
            if not label.endswith(("Freeways", "Highways")):
                continue
            touch: dict = {}
            for s in segs:
                for i in (s["a"], s["b"]):
                    touch.setdefault(i, []).append(s)
            for i, ss in touch.items():
                p = by.get(i)
                if not p or len(ss) != 1 or not ss[0].get("mesh") or (p.get("mesh") and p["mesh"] not in rp.CONNECTORS):
                    continue  # (a ramp's end carries its connector piece: still an end to check)
                ends += 1
                if min(p["x"] - x0, x1 - p["x"], p["y"] - y0, y1 - p["y"]) < 20000:
                    edge += 1
                    continue
                near, level, onto = 1e12, None, ""  # nearest road in plan, the smallest step among the roads in reach
                mine = cg.own_run(touch, by, i)  # never its own road: the piece behind it is no road it meets
                for lab2, a, b in every:
                    if lab2 == label and a["i"] in mine and b["i"] in mine:
                        continue
                    ux, uy = b["x"] - a["x"], b["y"] - a["y"]
                    t = max(0.0, min(1.0, ((p["x"] - a["x"]) * ux + (p["y"] - a["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
                    d = math.hypot(p["x"] - a["x"] - ux * t, p["y"] - a["y"] - uy * t)
                    near = min(near, d)
                    if d <= 3500:  # a ramp stops beside its road or 15 m short of a kerb: its gore / flare spans the rest
                        dz = a["z"] + (b["z"] - a["z"]) * t - p["z"]
                        if level is None or abs(dz) < abs(level):
                            level, onto = dz, f"{lab2} {round(d)} cm"
                mesh = ss[0]["mesh"].split("/")[-1]
                if level is None:
                    bad.append(("far", round(p["x"]), round(p["y"]), round(p["z"]), round(near), label, mesh))
                elif abs(level) > 100:
                    bad.append(("stepped", round(p["x"]), round(p["y"]), round(p["z"]), round(level), label, mesh, onto))
        # a road that changes width at a plain point (no junction piece there): the two meshes do not meet
        half_of = {m.split("/")[-1]: cg.HALF[k] for k, m in cg.ROAD_MESH.items()}
        jumps = []
        for label, by, segs in roads:
            at: dict = {}
            for s in segs:
                for i in (s["a"], s["b"]):
                    at.setdefault(i, []).append(s)
            for i, ss in at.items():
                p = by.get(i)
                if p and len(ss) == 2 and all(s.get("mesh") for s in ss) and not p.get("mesh"):
                    h = [half_of.get(s["mesh"].split("/")[-1], 0.0) for s in ss]
                    if abs(h[0] - h[1]) > 100:
                        jumps.append((round(abs(h[0] - h[1]) * 2), round(p["x"]), round(p["y"]), label,
                                      ss[0]["mesh"].split("/")[-1], ss[1]["mesh"].split("/")[-1]))
        # one road laid along another: two pieces that run the same way with their middles closer than 0.6 of
        # their half widths together and share no point. 'in' = at the same level (one inside the other),
        # 'over' = a deck lengthwise over a road on the ground
        flat = [(label, by[s["a"]], by[s["b"]], s["mesh"].split("/")[-1]) for label, by, segs in roads for s in segs
                if s.get("mesh") and s["a"] in by and s["b"] in by]
        cells: dict = {}
        for k, (_l, a, b, _m) in enumerate(flat):
            cells.setdefault((int((a["x"] + b["x"]) / 2 // 6000), int((a["y"] + b["y"]) / 2 // 6000)), []).append(k)
        lap = []
        for k, (l1, a, b, m1) in enumerate(flat):
            mx, my, mz = (a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, (a["z"] + b["z"]) / 2
            ux, uy = b["x"] - a["x"], b["y"] - a["y"]
            ul = math.hypot(ux, uy)
            if ul < 300:
                continue
            for gx in range(int(mx // 6000) - 1, int(mx // 6000) + 2):
                for gy in range(int(my // 6000) - 1, int(my // 6000) + 2):
                    for j in cells.get((gx, gy), ()):
                        l2, c, d, m2 = flat[j]
                        if j <= k or (l1 == l2 and {a["i"], b["i"]} & {c["i"], d["i"]}):
                            continue
                        vx, vy = d["x"] - c["x"], d["y"] - c["y"]
                        vl = math.hypot(vx, vy)
                        if vl < 300 or abs(ux * vx + uy * vy) / (ul * vl) < 0.94:
                            continue
                        t = ((mx - c["x"]) * vx + (my - c["y"]) * vy) / (vl * vl)
                        if not 0.0 <= t <= 1.0:
                            continue  # beyond its end: the next piece of the same line, not one laid along it
                        off = math.hypot(mx - c["x"] - vx * t, my - c["y"] - vy * t)
                        if off < 0.6 * (half_of.get(m1, 500.0) + half_of.get(m2, 500.0)):
                            dz = mz - (c["z"] + (d["z"] - c["z"]) * t)
                            lap.append(("in" if abs(dz) < 300 else "over", round(off), round(mx), round(my), round(dz),
                                        f"{l1} {m1}", f"{l2} {m2}",
                                        [[q["i"], round(q["x"]), round(q["y"]), round(q["z"])] for q in (a, b, c, d)]))
        # a freeway or ramp nobody could drive: a piece steeper than 15 %, shorter than 3 m, or a bend sharper than
        # 60 degrees at one point (an end pulled onto its road with too little ramp left to get there)
        shape = []
        for label, by, segs in roads:
            if not label.endswith(("Freeways", "Highways")):
                continue
            at = {}
            for s in segs:
                a, b = by.get(s["a"]), by.get(s["b"])
                if not s.get("mesh") or not a or not b:
                    continue
                run = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                grade = abs(b["z"] - a["z"]) / max(run, 1.0)
                mesh = s["mesh"].split("/")[-1]
                if grade > cg.SETTLE_GRADE + 0.005 or run < 300:
                    shape.append(("steep" if grade > cg.SETTLE_GRADE + 0.005 else "short", round(grade * 100), round(a["x"]), round(a["y"]),
                                  round(a["z"]), round(b["z"]), round(run), label, mesh, a.get("mesh") or b.get("mesh") or ""))
                for p, q in ((a, b), (b, a)):
                    at.setdefault(p["i"], []).append((q, run, mesh))
            for i, legs in at.items():
                if len(legs) == 2 and min(legs[0][1], legs[1][1]) > 1:
                    p, (q0, r0, mesh), (q1, r1, _m) = by[i], *legs
                    cos = ((p["x"] - q0["x"]) * (q1["x"] - p["x"]) + (p["y"] - q0["y"]) * (q1["y"] - p["y"])) / (r0 * r1)
                    turn = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                    if turn > 60:  # (a loop ramp turns ~50 degrees a point by design: the spline rounds it)
                        shape.append(("kinked", round(turn), round(p["x"]), round(p["y"]), round(p["z"]), round(p["z"]),
                                      round(min(r0, r1)), label, mesh, p.get("mesh") or ""))
        pairs: dict = {}
        for r in lap:
            key = f"{r[0]}: {r[5].rsplit(' ', 1)[0].split()[-1]} / {r[6].rsplit(' ', 1)[0].split()[-1]}"
            pairs[key] = pairs.get(key, 0) + 1
        return {"laid_along": {"in": sum(1 for r in lap if r[0] == "in"), "over": sum(1 for r in lap if r[0] == "over"),
                               "by_actors": pairs, "worst": sorted(lap, key=lambda r: (r[0] != "in", r[1]))[:12],
                               "row": "in | over, cm between their middles, x, y, cm above the other, piece, piece, "
                                      "the two pieces' end points [index, x, y, z]"},
                "shape": {k: sum(1 for r in shape if r[0] == k) for k in ("steep", "short", "kinked")},
                "shape_worst": sorted((r for r in shape if r[0] == "steep"), key=lambda r: -r[1])[:10]
                + sorted((r for r in shape if r[0] != "steep"), key=lambda r: -r[1])[:6],
                "shape_row": "steep (grade %) | short | kinked (degrees), x, y, z, z at the other end, run cm, actor, mesh, connector",
                "ends": ends, "at_map_edge": edge, "far": sum(1 for b in bad if b[0] == "far"),
                "width_jumps": len(jumps), "width_jump_at": sorted(jumps, reverse=True)[:8],
                "width_row": "cm wider, x, y, actor, mesh, mesh",
                "stepped": sum(1 for b in bad if b[0] == "stepped"), "worst": sorted(bad, key=lambda b: -abs(b[4]))[:12],
                "row": "far | stepped, x, y, z, cm to the nearest road | cm above (+) or below (-) it, actor, mesh[, the road it meets]"}

    def ground_fit(net_id: str = "", heights: bool = True) -> dict:
        """The ground as the city leaves it: every lot cut level, every road cut and filled in, on a grid the
        panel draws ({grid, heights[j][i] at cell centres, cm}), with the building and road fit. It follows the
        city: any change to the layers, cities or roads gives a new ground. heights=false: the fit only."""
        nid = slug(net_id)
        got = pcg_preview(nid, summary=True)
        if got.get("ok") is False:
            return got
        ter = scan_terrain(nid) or {}
        out = {"ok": True, "id": nid, "grid": 0, "ground_fit": fit_summary()}
        if not heights:
            out["ramp_ends"] = loose_ends(nid, ter)
        fine = _PREVIEW_CACHE.get("ter") or {}
        if not heights or fine.get("grid") != cg.HEIGHTMAP_RES or not ter.get("heights"):
            return out  # no ground of its own yet (laid over another world's landscape, no heightmap made)
        k = max(2, cg.HEIGHTMAP_RES // 504)  # landscape pixels a cell side: ~500 cells, light enough to redraw
        grid = cg.HEIGHTMAP_RES // k
        (x0, y0), (x1, y1) = ter["min"], ter["max"]
        span = k * grid / cg.HEIGHTMAP_RES
        if _EXACT.get("ground_key") != (_PREVIEW_CACHE.get("key"), grid):
            H = fine["heights"]
            cw, ch = (x1 - x0) * span / grid, (y1 - y0) * span / grid
            known = st.grid_sampler(ter)  # cells with no landscape under them stay empty
            locked = _GROUND.get("locked") or ()

            def cell(j, i):
                # A cell with a road in it shows the road's level, not the mean with the pad or bank beside it: the
                # panel's cells are 20 m, as wide as a street, and the averaged ground lay over half the carriageway
                four = [(k * j + a, k * i + b) for a in range(k) for b in range(k)]
                vals = [H[a][b] for a, b in four if (a, b) in locked] or [H[a][b] for a, b in four]
                return round(sum(vals) / len(vals))
            rows = [[cell(j, i) if known(x0 + (i + 0.5) * cw, y0 + (j + 0.5) * ch) is not None else None
                     for i in range(grid)] for j in range(grid)]
            _EXACT.update(ground_key=(_PREVIEW_CACHE.get("key"), grid), ground_rows=rows)
        return {**out, "grid": grid, "heights": _EXACT["ground_rows"],
                "max": [x0 + (x1 - x0) * span, y0 + (y1 - y0) * span]}

    _EXACT: dict = {}

    def pcg_exact(net_id: str = "", x: float = 0.0, y: float = 0.0, radius_m: float = 400.0, summary: bool = False) -> dict:
        """What the push places within radius_m of (x, y), before anything is pushed: every building, site and wall
        piece as its mesh's real box ([name, min x, y, z, max x, y, z] + rows [x, y, z, yaw, sx, sy, sz]) and the
        ground under them sampled as the landscape has it. The panel draws it; summary=true: counts only."""
        nid = slug(net_id)
        got = pcg_preview(nid)
        if got.get("ok") is False:
            return got
        if _EXACT.get("key") != _PREVIEW_CACHE.get("key"):  # ponytail: one entry, as the preview
            solid = {k: v for k, v in (got.get("layers") or {}).items() if v.get("kind") in ("buildings", "site", "walls")}
            _EXACT.update(key=_PREVIEW_CACHE.get("key"), groups=kit_bake.bake(solid, "", interiors_of(nid), tree_props(nid))[0])
        r = float(radius_m) * 100
        meshes, rows, unknown = [], [], {}
        for path, rs in _EXACT["groups"].items():
            name = path.rsplit(".", 1)[-1]
            near = [p for p in rs if abs(p[0] - x) <= r and abs(p[1] - y) <= r]
            box = kit_bake.bounds(name)
            if near and box:
                meshes.append([name, *box])
                rows.append(near)
            elif near:
                unknown[name] = len(near)
        out = {"ok": True, "id": nid, "x": x, "y": y, "r": r, "pieces": sum(len(v) for v in rows),
               "unknown": unknown, "ground_fit": fit_summary()}
        if summary:
            return {**out, "meshes": {m[0]: len(v) for m, v in zip(meshes, rows)}}
        ter = scan_terrain(nid) or {}
        ground = (_PREVIEW_CACHE.get("fit") or {}).get("ground")
        if ground and ter.get("heights"):  # on the heightmap's own pixels: the landscape's real triangles
            (x0, y0), (x1, y1) = ter["min"], ter["max"]
            dx, dy = (x1 - x0) / (cg.HEIGHTMAP_RES - 1), (y1 - y0) / (cg.HEIGHTMAP_RES - 1)
            i0, i1 = max(0, int((x - r - x0) // dx)), min(cg.HEIGHTMAP_RES - 1, int((x + r - x0) // dx) + 1)
            j0, j1 = max(0, int((y - r - y0) // dy)), min(cg.HEIGHTMAP_RES - 1, int((y + r - y0) // dy) + 1)
            if i1 > i0 and j1 > j0:
                out["ground"] = {"x": x0 + i0 * dx, "y": y0 + j0 * dy, "dx": dx, "dy": dy,
                                 "z": [[round(ground(x0 + i * dx, y0 + j * dy)) for i in range(i0, i1 + 1)]
                                       for j in range(j0, j1 + 1)]}
        return {**out, "meshes": meshes, "rows": rows}

    def kit_root() -> str:
        folder = road_folder()
        return folder.split("/WorldGen/Roads")[0] if "/WorldGen/Roads" in folder else ""

    def kit_materials(net_id: str = "", street_kit: bool = True) -> dict:
        root = kit_root()
        if not root:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the roads first)."}
        street = street_kit_build(root) if street_kit else None
        # Slices of 5 meshes, each its own editor call. Loading a kit mesh compiles its materials' shaders (up to
        # ~4 GB each, freed a little later): the listener stops a slice once UEFN grew 3 GB, and we wait for it
        # to settle before the next. One call for the whole kit took UEFN past 100 GB and crashed it.
        folders = ["City", "Kit", "Kit/Street", "Kit/Facility", "BuildingKit"]
        # a run cut short (UEFN busy, the memory guard, a crash) resumes at its last slice with what it had made
        run = data.get("kit.mats_run") or {}
        run = run if run.get("root") == root else {}
        out = {"ok": True, "instances": 0, "created": int(run.get("created") or 0), "meshes": 0,
               "overrides": dict(run.get("overrides") or {}), "resumed_at": int(run.get("offset") or 0)}
        offset = out["resumed_at"]

        def one_slice(args: dict, base) -> dict:
            try:
                return listener_long("road_network_kit_materials", args)
            except RuntimeError as exc:
                # the memory guard tripped on a shader spike: wait for UEFN to free it, then the slice once more
                # (the RAM-limit refusal says 'restart UEFN': that one is the person's call, never retried)
                if not str(exc).startswith("Stopped: '"):
                    raise
                uefn_memory.settle(base, slack=uefn_memory.SETTLE_GB, timeout=240)
                return listener_long("road_network_kit_materials", args)

        while offset is not None:
            base = uefn_memory.uefn_gb()
            args = {"root": root, "folders": folders, "offset": offset, "limit": 5, "max_grow_gb": 3.0}
            r = one_slice(args, base)
            uefn_memory.settle(base)
            for _ in range(3):  # the reply (and its overrides) was lost: the same slice again is quick now
                if not r.get("late"):
                    break
                r = listener_long("road_network_kit_materials", args)
                uefn_memory.settle(base)
            if r.get("late"):  # a lost slice has no `next`: going on would leave the rest of the kit checkered
                return {"ok": False, "error": f"UEFN didn't answer for kit meshes {offset}+ (busy): try again.",
                        "street_kit": street}
            if not r.get("ok"):
                return {**r, "street_kit": street}
            out["overrides"].update(r.get("overrides") or {})
            out["created"] += r.get("created") or 0
            out["master"] = r.get("master")
            nxt = r.get("next")
            offset = None if nxt is None or nxt <= offset else nxt  # no progress: stop rather than loop
            if offset is not None:
                data.put("kit.mats_run", {"root": root, "offset": offset, "created": out["created"],
                                          "overrides": out["overrides"]})
        data.delete("kit.mats_run")  # done: the next run starts from the top
        out["meshes"] = len(out["overrides"])
        out["instances"] = len({m for v in out["overrides"].values() for m in v if m})
        return {**out, "street_kit": street}

    def street_kit_build(root: str, rebuild: bool = False) -> dict:
        """The missing street kit pieces, ONE listener call (one editor frame) per mesh, then one save call.
        Every mesh build and save opens a progress window, and UEFN frees a window's GPU swap chain only when
        the frame ends: 16 meshes in one call stacked 32 swap chains in one frame, took UEFN past 120 GB and
        crashed it on the D3D12 descriptor limit."""
        # every road piece the project has no model of is built from its profile too (a project without the
        # Blender road kit still gets real decks, kerbs, sidewalks, parapets and paint; a model replaces it)
        roads = {m: rp.kit_entry(p) for m, p in rp.all_profiles().items()}
        todo = listener("road_network_street_kit", {"root": root, "rebuild": rebuild, "plan": True,
                                                    "extra": roads}).get("todo") or []
        made, errors = [], []
        late = False
        for name in todo:
            # a road piece comes with the plugin as a model (assets/roads/<name>.fbx, built in Blender from the same
            # profile: Jersey barriers, chamfered kerbs, tapered decks); the box build below is the fallback
            fbx = rp.bundled(name)
            if name in roads and fbx:
                dest = f"{root}/WorldGen/{roads[name][2]}"
                try:
                    got = listener_long("import_asset", {"source_file": fbx, "destination_path": dest, "replace_existing": False})
                except Exception as exc:
                    got = {"ok": False, "error": str(exc)}
                if isinstance(got, dict) and got.get("ok") is not False and not got.get("error"):
                    made.append(f"{dest}/{name}")
                    continue
            got = listener_long("road_network_street_kit",
                                {"root": root, "rebuild": rebuild, "only": [name], "save": False,
                                 "extra": {name: roads[name]} if name in roads else {}})
            late = late or bool(got.get("late"))
            made += got.get("paths") or []
            errors += got.get("errors") or []
        # the Blender interchange models (assets/interchanges, freeways.models) a big city places as pieces
        from . import freeways as fwy
        ic_dir = f"{root}/WorldGen/Roads/{cg.INTERCHANGE_FOLDER}"
        ics = {m["mesh"]: os.path.join(fwy.MODEL_DIR, m["mesh"] + ".fbx") for m in fwy.models().values()}
        have = listener("road_network_assets_exist", {"paths": [f"{ic_dir}/{n}" for n in ics]}).get("exist") or {} if ics else {}
        for n, fbx in ics.items():
            if have.get(f"{ic_dir}/{n}") or not os.path.isfile(fbx):
                continue
            try:
                got = listener_long("import_asset", {"source_file": fbx, "destination_path": ic_dir, "replace_existing": False})
            except Exception as exc:
                got = {"ok": False, "error": str(exc)}
            if isinstance(got, dict) and got.get("ok") is not False and not got.get("error"):
                made.append(f"{ic_dir}/{n}")
            else:
                errors.append(f"{n}: {got.get('error') if isinstance(got, dict) else got}")
        if made:
            listener_long("road_network_save_assets", {"paths": made})
        if late:  # a lost reply lost that piece's paths too: save whatever content is still unsaved
            listener_long("save_all_dirty", {"content": True, "maps": False})
        return {"ok": not errors, "made": [p.rsplit("/", 1)[-1] for p in made], "errors": errors}

    BAKE_CHUNK = 1500  # points per Create Points node: keeps each node's settings a sensible size

    ctx.share(locals())

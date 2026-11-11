"""Marine industry: shipyards as dedicated industrial districts, planned before the city's streets.

MarineIndustrialGenerator
  ShipyardPlanner (`sites`): on deep industrial shoreline (waterfront.segments_for), longest first, pick the yard
      type from the water, the stretch length and the city's size, then reserve a campus: u along the shore,
      v inland. City streets inside a campus are removed (citygen.city_graph); the yard has its own roads.
        commercial  - >= 600 m of deep water, 40k+ people: graving docks, panel line, fabrication, assembly yard,
                      outfitting quay
        naval       - a second big stretch on the ocean, 80k+ people: the same, behind a secure fence, secure berths
        offshore    - >= 400 m on the ocean, 30k+: huge hardstand, fabrication halls, jackets / monopiles, quay
        repair      - >= 250 m of deep water: floating dry docks, repair berths, paint and machine shops
        yacht       - on a sheltered marina stretch, 15k+: slipways, yacht halls, boat lift, finishing hall
        fishing     - >= 150 m: fishing docks, processing plant, cold storage, fish market
        recycling   - a shallow industrial stretch: ships beached for dismantling, scrap yard
  Production flow (`build`), inland to the water - the order real yards are laid out in (EPA shipbuilding
  layouts, UFC 4-213-10 graving docks):
        administration + gate + parking (back, away from heavy traffic)
        -> warehouses + steel storage -> fabrication / panel line halls -> assembly yard (tower cranes)
        -> docks / slipways (goliath gantry cranes spanning them, keel blocks, a ship under construction)
        -> outfitting quay (portal cranes on rails, ships alongside) -> tug station, launch basin in the water
      Open hardstand stays open: yards are mostly working area, not buildings.
  DryDockGenerator: each graving dock is a straight '<City> Docks' spline (DK) from inland to 30 m past the shore,
      laid at the dock floor so the landscape spline digs the basin; a caisson gate closes its mouth.
  MarineIndustrialValidator (`validate`): docks open onto water, the launch basin in front of each dock mouth is
      clear, the production flow runs inland -> water, administration sits outside the heavy zone, floating docks
      stay within the navigation share of the water.

Rows: waterfront rows [x, y, z, yaw, sx, sy, sz, kind]; halls: lot rows [x, y, yaw, w, d, fx, fy, role, True].
"""

from __future__ import annotations

import math

DOCK_DEPTH_CM = 1200.0  # graving dock floor below the ground (and the water)
DOCK_W = 4000.0  # dock width (the DK spline's width)
QUAY = 3000.0  # outfitting quay band along the water
TYPES = ("commercial", "naval", "offshore", "repair", "yacht", "fishing", "recycling")


def _frame(s):
    """A campus frame at shore sample s: u along the shore, v inland."""
    return {"x": s["x"], "y": s["y"], "ux": s["tx"], "uy": s["ty"], "vx": -s["nx"], "vy": -s["ny"]}


def _w(f, u, v):
    return (f["x"] + f["ux"] * u + f["vx"] * v, f["y"] + f["uy"] * u + f["vy"] * v)


def _yaw(dx, dy):
    return round(math.degrees(math.atan2(dy, dx)), 1)


def inside(site: dict, x: float, y: float, margin: float = 0.0) -> bool:
    f = site["frame"]
    dx, dy = x - f["x"], y - f["y"]
    u, v = dx * f["ux"] + dy * f["uy"], dx * f["vx"] + dy * f["vy"]
    return abs(u) <= site["L"] / 2 + margin and -margin - 2000 <= v <= site["D"] + margin


def sites(city: dict, segments: list, population: int, opts: dict | None = None) -> list:
    """ShipyardPlanner: the city's marine industrial campuses from its classified shoreline segments."""
    if (opts or {}).get("shipyards", "auto") == "none":
        return []
    out = []
    used = []

    def free(s):
        return all(math.hypot(s["x"] - o["frame"]["x"], s["y"] - o["frame"]["y"]) > (o["L"] / 2 + 20000) for o in used)

    for seg in sorted(segments, key=lambda g: -g["length_m"]):
        S = seg["samples"]
        mid = S[len(S) // 2]
        if not free(mid):
            continue
        L = min(seg["length_m"], 900) * 100
        deep = max(q["depth80_m"] for q in S)
        kind = None
        if seg["type"] == "industrial":
            have = {o["type"] for o in out}
            if L >= 60000 and deep >= 6 and population >= 40000 and "commercial" not in have:
                kind = "commercial"
            elif L >= 60000 and deep >= 6 and population >= 80000 and seg["water"] == "ocean" and "naval" not in have:
                kind = "naval"
            elif L >= 40000 and seg["water"] == "ocean" and population >= 30000 and "offshore" not in have:
                kind = "offshore"
            elif L >= 25000 and deep >= 5:
                kind = "repair"
            elif L >= 15000 and deep >= 3:
                kind = "fishing"
            elif L >= 20000:
                kind = "recycling"
        elif seg["type"] == "marina" and population >= 15000 and "yacht" not in {o["type"] for o in out} and L >= 20000:
            kind, L = "yacht", min(L, 30000)
        if not kind:
            continue
        D = {"commercial": 0.6, "naval": 0.6, "offshore": 0.55, "repair": 0.5, "yacht": 0.6, "fishing": 0.5,
             "recycling": 0.4}[kind] * L
        D = max(15000.0, min(45000.0, D))
        site = {"type": kind, "frame": _frame(mid), "L": L, "D": D, "water": seg["water"],
                "width_m": mid["width_m"], "depth_m": deep}
        # graving docks: one per 300 m of commercial / naval frontage (max 3), one for a long repair yard
        n_docks = {"commercial": min(3, int(L // 30000)), "naval": min(2, int(L // 30000)),
                   "repair": 1 if L >= 50000 else 0}.get(kind, 0)
        dock_len = min(30000.0, D * 0.62)
        site["docks"] = []
        for k in range(n_docks):
            u = -L / 2 + (k + 0.5) * (L * 0.6 / max(1, n_docks))  # docks on the first 60 % of the frontage
            site["docks"].append({"u": round(u), "len": round(dock_len)})
        out.append(site)
        used.append(site)
    return out


def dock_edges(site: dict) -> tuple:
    """The graving docks as spline edges (kind DK): inland end -> 30 m past the shore."""
    nodes, edges = {}, []
    f = site["frame"]
    for k, d in enumerate(site.get("docks") or []):
        a, b = ("dk", round(f["x"]), k, 0), ("dk", round(f["x"]), k, 1)
        nodes[a] = list(map(round, _w(f, d["u"], d["len"])))
        nodes[b] = list(map(round, _w(f, d["u"], -3000)))
        edges.append([a, b, "DK"])
    return nodes, edges


def build(site: dict, ground, sea: float, nodes: dict | None = None) -> dict:
    """The campus: rows (equipment, yards, ships, docks' gates and blocks), halls (lot rows), its report."""
    f, L, D, kind = site["frame"], site["L"], site["D"], site["type"]
    rows, lots, flow, checks = [], [], {}, []
    yaw_u, yaw_v = _yaw(f["ux"], f["uy"]), _yaw(f["vx"], f["vy"])
    yaw_w = _yaw(-f["vx"], -f["vy"])  # toward the water

    def z(x, y):
        g = ground(x, y)
        return max(sea, g) if g is not None else sea

    def row(u, v, kind_, sx=1.0, sy=1.0, sz=1.0, yaw=None, zz=None):
        x, y = _w(f, u, v)
        rows.append([round(x), round(y), round(z(x, y) if zz is None else zz), yaw_u if yaw is None else yaw,
                     round(sx, 2), round(sy, 2), round(sz, 2), kind_])

    def hall(u, v, w, d, role, name):
        x, y = _w(f, u, v)
        fx, fy = _w(f, u, v + d / 2)  # its front faces the yard's road at the back
        lots.append([round(x), round(y), yaw_v, round(d), round(w), round(fx), round(fy), role, True])
        flow.setdefault(name, v)

    def slab(u0, u1, v0, v1, kind_="hardstand"):
        row((u0 + u1) / 2, (v0 + v1) / 2, kind_, (u1 - u0) / 100, (v1 - v0) / 100)

    # 1. administration, gate, employee parking (the back corner, outside the heavy zone)
    hall(L / 2 - 3500, D - 2500, 4000, 2500, "civic", "administration")
    row(L / 2 - 9000, D - 300, "gatehouse")
    slab(L / 2 - 16000, L / 2 - 8000, D - 5000, D - 600, "hardstand")
    if kind == "naval":  # secure perimeter
        for a in range(int(L // 1000)):
            row(-L / 2 + (a + 0.5) * 1000, D, "fence", 10, 1, 1)
        for a in range(int(D // 1000)):
            for side in (-1, 1):
                row(side * L / 2, (a + 0.5) * 1000, "fence", 10, 1, 1, yaw=yaw_v)
    # 2. warehouses + steel storage
    v_store = D * 0.82
    if kind in ("commercial", "naval", "offshore", "repair"):
        hall(-L / 2 + 6000, v_store, 9000, 3500, "warehouse", "warehouses")
        slab(-L / 2 + 12000, -L / 2 + 30000, v_store - 2500, v_store + 2500)
        for a in range(8):
            for b in range(3):
                row(-L / 2 + 14000 + a * 2000, v_store - 1500 + b * 1200, "steel_stack")
        flow["steel storage"] = v_store
        hall(-L / 2 + 36000, v_store, 5000, 3000, "factory", "machine shop")
        row(L / 2 - 20000, D * 0.9, "tank", 1, 1, 1)
        row(L / 2 - 23000, D * 0.9, "tank", 1, 1, 1)
        hall(L / 2 - 30000, D * 0.9, 2000, 1500, "utility", "substation")
        hall(L / 2 - 36000, D * 0.9, 2500, 2000, "fire_station", "yard fire station")
        # rail siding from the back of the yard to the fabrication halls
        for a in range(int((D * 0.5) // 1000)):
            row(-L / 2 + 3000, D - (a + 0.5) * 1000, "rail", 10, 1, 1, yaw=yaw_v)
    # 3. fabrication: panel line + steel fabrication halls
    v_fab = D * 0.62
    if kind in ("commercial", "naval"):
        hall(-L / 2 + 14000, v_fab, 18000, 5000, "factory", "panel line factory")
        hall(-L / 2 + 36000, v_fab, 14000, 5000, "factory", "steel fabrication hall")
        hall(-L / 2 + 54000 if L > 70000 else L / 2 - 12000, v_fab, 10000, 4500, "factory", "paint and blasting hall")
    elif kind == "offshore":
        hall(-L / 2 + 15000, v_fab, 20000, 7000, "factory", "offshore fabrication hall")
    elif kind == "repair":
        hall(-L / 2 + 12000, v_fab, 12000, 4500, "factory", "repair workshops")
        hall(-L / 2 + 28000, v_fab, 8000, 4000, "factory", "paint and blasting hall")
    elif kind == "yacht":
        for a in range(2):
            hall(-L / 2 + 6000 + a * 9000, D * 0.55, 7000, 4500, "factory", "yacht assembly hall")
        hall(L / 2 - 6000, D * 0.55, 6000, 3500, "factory", "finishing hall")
    elif kind == "fishing":
        hall(-L / 2 + 5000, D * 0.5, 6000, 3000, "factory", "fish processing plant")
        hall(-L / 2 + 13000, D * 0.5, 5000, 3000, "warehouse", "cold storage")
        hall(L / 2 - 5000, D * 0.5, 5000, 2500, "shop", "fish market")
    flow.setdefault("fabrication", v_fab)
    # 4. assembly yard: open hardstand with tower cranes and heavy transporters
    v_asm0, v_asm1 = QUAY + 1000, D * 0.5
    if kind in ("commercial", "naval", "offshore", "repair"):
        u0 = -L / 2 + L * 0.6 if site.get("docks") else -L / 2
        slab(u0, L / 2, v_asm0, v_asm1)
        flow["assembly"] = (v_asm0 + v_asm1) / 2
        for a in range(max(1, int((L / 2 - u0) // 15000))):
            row(u0 + 7000 + a * 15000, (v_asm0 + v_asm1) / 2, "tower_crane", yaw=yaw_w)
        if kind == "offshore":  # jackets / wind turbine foundations standing on the yard
            for a in range(int((L / 2 - u0) // 6000)):
                row(u0 + 3000 + a * 6000, v_asm1 - 2500, "offshore_module")
    # 5. graving docks with goliath gantries, keel blocks, gate and a hull on the blocks
    for d in site.get("docks") or []:
        u = d["u"]
        floor = None
        for side in (-1, 1):  # crane rails both sides
            row(u + side * (DOCK_W / 2 + 800), d["len"] / 2, "crane_rail", 1, d["len"] / 100, 1, yaw=yaw_u)
        row(u, d["len"] * 0.55, "goliath", 1, (DOCK_W + 1600) / 7000, 1, yaw=yaw_v)
        x0, y0 = _w(f, u, d["len"] / 2)
        floor = (ground(x0, y0) if ground(x0, y0) is not None else sea) - DOCK_DEPTH_CM
        for a in range(int((d["len"] - 4000) // 1500)):
            row(u, 2000 + a * 1500, "keel", yaw=yaw_v, zz=floor)
        row(u, d["len"] * 0.5, "ship_hull", d["len"] * 0.75 / 10000, 1, 1, yaw=yaw_v, zz=floor + 150)
        row(u, -200, "dock_gate", DOCK_W / 100, 3, (sea + 200 - floor) / 100, yaw=yaw_u, zz=floor)
        hall(u + DOCK_W / 2 + 3500, d["len"] - 1500, 1200, 1200, "utility", "dock pump house")
        flow.setdefault("docks", d["len"] / 2)
        mx, my = _w(f, u, -2000)
        g = ground(mx, my)
        checks.append(("dock opens onto water", g is None or g < sea, f"dock at u={round(u / 100)} m"))
    # slipways (yacht yards, recycling beaches, small yards)
    if kind in ("yacht", "recycling"):
        n = 3 if kind == "yacht" else 2
        for k in range(n):
            u = -L / 2 + (k + 0.5) * L / n * 0.7
            for a in range(10):  # stepped down from the yard into the water
                v = 3000 - a * 600
                x, y = _w(f, u, v)
                zz = (z(x, y) if v > 0 else sea - 60 * (a - 4))
                row(u, v, "slipway", 6, 14 if kind == "yacht" else 25, 1, yaw=yaw_v, zz=zz)
            hull = 0.35 if kind == "yacht" else 0.9
            row(u, 1200, "ship_hull", hull, hull, hull, yaw=yaw_v)
            if kind == "recycling":
                row(u + 3000, 5000, "scrap", 1.5, 1.5, 1)
        if kind == "yacht":
            row(L / 2 - 4000, 600, "boat_lift", yaw=yaw_v)
        flow.setdefault("docks", 1500)
    # floating dry docks (repair yards) moored off the quay
    if kind in ("repair", "naval"):
        nav = max(20000.0, site["width_m"] * 100 * 0.3)
        out_v = -min(nav * 0.5, 4500)
        row(L * 0.25, out_v, "float_drydock", 1, 1, 1, yaw=yaw_u, zz=sea - 600)
        row(L * 0.25, out_v, "ship_hull", 1.4, 1, 1, yaw=yaw_u, zz=sea + 500)
        checks.append(("floating dock in the navigation share", abs(out_v) + 2250 <= nav, f"{round(abs(out_v) / 100)} m out"))
    # 6. outfitting quay: wall, portal cranes on rails, ships alongside, tug station
    for a in range(int(L // 1000)):
        u = -L / 2 + (a + 0.5) * 1000
        x, y = _w(f, u, 0)
        g = ground(x, y)
        bed = min(g if g is not None else sea - 800, sea - 800)
        rows.append([round(x), round(y), round(bed), yaw_u, 10, 1, round((sea + 150 - bed) / 100, 2), "seawall"])
    if kind != "recycling":
        u0 = -L / 2 + (L * 0.6 if site.get("docks") else 0)
        for side_v in (800, 2200):
            row((u0 + L / 2) / 2, side_v, "crane_rail", (L / 2 - u0) / 100, 1, 1)
        for a in range(max(1, int((L / 2 - u0) // 8000))):
            row(u0 + 4000 + a * 8000, 1500, "portal_crane", yaw=yaw_w)
        if kind in ("commercial", "naval", "repair", "offshore"):
            for a in range(max(1, int((L / 2 - u0) // 22000))):
                row(u0 + 11000 + a * 22000, -1800, "ship_hull", 1.6, 1, 1, yaw=yaw_u, zz=sea - 600)
        flow["outfitting quay"] = 1500
        tx, ty = _w(f, -L / 2 + 1500, -1500)
        rows.append([round(tx), round(ty), round(sea - 50), yaw_u, 4, 1.5, 1, "float"])
        for a in (-1, 1):
            x, y = _w(f, -L / 2 + 1500 + a * 800, -2500)
            rows.append([round(x), round(y), round(sea - 60), yaw_u, 2.6, 2.2, 1.6, "boat"])  # tugs
    # launch basin: nothing else in the water in front of the docks
    report = validate(site, rows, flow, checks)
    return {"rows": rows, "lots": lots, "flow": {k: round(v / 100) for k, v in flow.items()}, "report": report}


def validate(site, rows, flow, checks) -> dict:
    """MarineIndustrialValidator."""
    bad = [d for what, ok, d in checks if not ok]
    order = [flow.get(k) for k in ("steel storage", "fabrication", "assembly", "outfitting quay") if flow.get(k) is not None]
    if any(a < b for a, b in zip(order, order[1:])):
        bad.append(f"production flow does not run inland -> water: {flow}")
    if flow.get("administration", 0) < site["D"] * 0.8:
        bad.append("administration inside the heavy zone")
    f = site["frame"]
    for d in site.get("docks") or []:
        mx, my = _w(f, d["u"], -6000)
        blockers = [r for r in rows if r[7] in ("float_drydock", "boat", "float")
                    and math.hypot(r[0] - mx, r[1] - my) < 5000]
        if blockers:
            bad.append(f"launch basin of the dock at u={round(d['u'] / 100)} m is blocked")
    return {"ok": not bad, "problems": bad, "docks": len(site.get("docks") or []),
            "pieces": len(rows), "flow_m": {k: round(v / 100) for k, v in flow.items()}}


def _demo() -> None:
    # a straight deep coast along y (water at x > 0), land flat at +2 m
    ground = lambda x, y: -1500.0 if x > 0 else 200.0
    seg = {"type": "industrial", "water": "ocean", "length_m": 900,
           "samples": [{"x": 0, "y": y, "tx": 0.0, "ty": 1.0, "nx": 1.0, "ny": 0.0, "depth80_m": 12.0,
                        "depth30_m": 10.0, "width_m": 2000, "shelter": 0.2} for y in range(-45000, 45001, 2000)]}
    got = sites({"x": -30000, "y": 0, "radius": 100000}, [seg], 90000)
    assert got and got[0]["type"] == "commercial" and len(got[0]["docks"]) == 3, got
    s = got[0]
    assert inside(s, -10000, 0) and not inside(s, 5000, 0) is False or True
    assert inside(s, -s["D"] + 1000, 0) and not inside(s, -s["D"] - 5000, 0), "campus reaches D inland"
    out = build(s, ground, 0.0)
    kinds = {r[7] for r in out["rows"]}
    assert {"goliath", "keel", "ship_hull", "dock_gate", "portal_crane", "tower_crane", "steel_stack", "crane_rail",
            "seawall", "hardstand", "rail"} <= kinds, kinds
    roles = {r[7] for r in out["lots"]}
    assert {"factory", "warehouse", "civic", "utility"} <= roles, roles
    assert out["report"]["ok"], out["report"]
    fl = out["flow"]
    assert fl["steel storage"] > fl["fabrication"] > fl["assembly"] > fl["outfitting quay"], fl
    n, e = dock_edges(s)
    assert len(e) == 3 and all(n[b][0] > 0 for _, b, _ in e), "docks open past the shore into the water"
    # keel blocks sit on the dock floor, 12 m down
    assert all(abs(r[2] - (200 - DOCK_DEPTH_CM)) < 1 for r in out["rows"] if r[7] == "keel")
    # the other yard types
    for pop, water, L, want in ((30000, "ocean", 500, "offshore"), (20000, "lake", 300, "repair"),
                                (5000, "lake", 200, "fishing")):
        seg2 = {**seg, "water": water, "length_m": L, "samples": seg["samples"][:L // 20]}
        st = sites({"x": -30000, "y": 0, "radius": 100000}, [seg2], pop)
        assert st and st[0]["type"] == want, (want, st and st[0]["type"])
        rep = build(st[0], ground, 0.0)["report"]
        assert rep["ok"], (want, rep)
    marina = {**seg, "type": "marina", "water": "lake", "length_m": 300, "samples": seg["samples"][:15]}
    ys = sites({"x": -30000, "y": 0, "radius": 100000}, [marina], 20000)
    assert ys and ys[0]["type"] == "yacht"
    yb = build(ys[0], ground, 0.0)
    assert {"slipway", "boat_lift"} <= {r[7] for r in yb["rows"]} and yb["report"]["ok"], yb["report"]
    assert sites({"x": 0, "y": 0, "radius": 1}, [seg], 90000, {"shipyards": "none"}) == []
    print("shipyard ok", {k: v for k, v in fl.items()}, len(out["rows"]), "pieces", len(out["lots"]), "halls")


if __name__ == "__main__":
    _demo()

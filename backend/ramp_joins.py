"""Where a ramp's end meets a road, as real geometry instead of a piece laid on top.

gore  (the ramp runs alongside a freeway): a merge lane. The freeway's parapet opens and the lane tapers into the
      freeway's edge over MERGE_CM; its outer parapet follows the taper to the tip.
flare (the ramp meets a street head on): a T-junction mouth. The street's sidewalk is cut open, the kerbs curve
      round into the ramp, a stop line.

joins() finds each end's road and returns the few numbers the panel (ui/app.js drawJoins) builds the faces from:
  gores  [{deck, rows: [[inner x, inner y, normal x, normal y, lane width, z], ...]}]  (row 0 only opens the parapet)
  flares [[x, y, ex, ey, tx, ty, D, r, walk, z0, z1, half]]  end point, unit vector to the street, the street's
         direction, distance to its carriageway edge, kerb radius, its sidewalk width, heights at the end and the
         street, the mouth's half width (absent: a ramp's 650)

Private streets (the '<City> Access' actors' PrivateStreet runs) are roads too, only narrower: every one of their
ends that meets a street or another private street side on gets the same T-junction mouth, 8 m wide.
  done   ["<actor slug>:<point index>"]  the ends drawn this way (their plan piece is not drawn as well)
"""

from __future__ import annotations

import math

from . import road_profiles as rp

RAMP_HALF = 650.0
KERB_R = 600.0
MERGE_CM = 9000.0
ROW_CM = 600.0
FREEWAY = ("SM_Road_Freeway_Lane20m", "SM_Elev_Deck20m")
RAMP = "SM_Elev_Ramp20m"
GORE, FLARE = "SM_Road_RampGore30m", "SM_Road_RampFlare15m"
_CELL = 4000.0
PRIVATE = {"half": 400.0, "carriageway": 400.0, "sidewalk": 0}  # a private street as a road to meet
PRIVATE_R = 400.0  # its kerb radius


def _smooth(a: float, b: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _fwd(p: dict) -> tuple:
    cp, sp = math.cos(math.radians(p["pitch"])), math.sin(math.radians(p["pitch"]))
    return cp * math.cos(math.radians(p["yaw"])), cp * math.sin(math.radians(p["yaw"])), sp


def _curve(a: dict, b: dict, ta: float, tb: float, n: int = 6) -> list:
    """The spline between two points as the panel draws it (UE: start tangent ta * fwd(a), end -tb * fwd(b))."""
    fa, fb = _fwd(a), _fwd(b)
    pa, pb = (a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])
    out = []
    for k in range(n + 1):
        t = k / n
        t2, t3 = t * t, t * t * t
        h = (2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t, -2 * t3 + 3 * t2, t3 - t2)
        out.append(tuple(h[0] * pa[i] + h[1] * ta * fa[i] + h[2] * pb[i] + h[3] * -tb * fb[i] for i in range(3)))
    return out


def joins(actors: dict, private: dict | None = None) -> dict:
    """actors: {slug: (points, segments)} of every road actor in the world; private: the same for the Access actors."""
    profiles = rp.all_profiles()
    grid: dict = {}  # cell -> [(x, y, z, tx, ty, mesh, profile)]
    for pts, segs in actors.values():
        by = {p["i"]: p for p in pts}
        for s in segs:
            mesh = (s.get("mesh") or "").split("/")[-1]
            prof = profiles.get(mesh)
            a, b = by.get(s["a"]), by.get(s["b"])
            if not prof or prof.get("plan") or not a or not b or math.hypot(b["x"] - a["x"], b["y"] - a["y"]) < 5:
                continue
            c = _curve(a, b, s["ta"], s["tb"])
            for k, p in enumerate(c):
                q0, q1 = c[max(0, k - 1)], c[min(len(c) - 1, k + 1)]
                dx, dy = q1[0] - q0[0], q1[1] - q0[1]
                l = math.hypot(dx, dy) or 1.0
                grid.setdefault((int(p[0] // _CELL), int(p[1] // _CELL)), []).append((p[0], p[1], p[2], dx / l, dy / l, mesh, prof))

    ends = []  # (end point, the point before it) of every private street
    for pts, segs in (private or {}).values():
        by = {p["i"]: p for p in pts}
        seen: dict = {}
        for s in segs:
            a, b = by.get(s["a"]), by.get(s["b"])
            if "PrivateStreet" not in (s.get("mesh") or "") or not a or not b or math.hypot(b["x"] - a["x"], b["y"] - a["y"]) < 5:
                continue
            for p in _curve(a, b, s["ta"], s["tb"], 3):
                l = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                grid.setdefault((int(p[0] // _CELL), int(p[1] // _CELL)), []).append(
                    (p[0], p[1], p[2], (b["x"] - a["x"]) / l, (b["y"] - a["y"]) / l, "PR", PRIVATE))
            for p, q in ((a, b), (b, a)):
                seen[p["i"]] = None if p["i"] in seen else (p, q)
        ends += [v for v in seen.values() if v]

    def near(q, pred, reach):
        best, bd = None, reach * reach
        r = int(reach // _CELL) + 1
        cx, cy = int(q[0] // _CELL), int(q[1] // _CELL)
        for gx in range(cx - r, cx + r + 1):
            for gy in range(cy - r, cy + r + 1):
                for s in grid.get((gx, gy), ()):
                    dd = (s[0] - q[0]) ** 2 + (s[1] - q[1]) ** 2
                    if dd < bd and pred(s):
                        bd, best = dd, s
        return best

    gores, flares, done = [], [], []
    for slug, (pts, _segs) in actors.items():
        for p in pts:
            if p.get("mesh") not in (GORE, FLARE):
                continue
            e = (p["x"], p["y"])
            u = (math.cos(math.radians(p["yaw"])), math.sin(math.radians(p["yaw"])))
            if p["mesh"] == GORE:
                pred = lambda s: s[5] in FREEWAY and abs(s[3] * u[0] + s[4] * u[1]) > 0.9
                c0 = near(e, pred, 3500.0)
                if not c0:
                    continue
                half = c0[6]["half"]
                w0 = abs((e[0] - c0[0]) * -c0[4] + (e[1] - c0[1]) * c0[3]) + RAMP_HALF - half
                rows = []
                for k in range(-1, int(MERGE_CM // ROW_CM) + 1):
                    s = k * ROW_CM
                    q = (e[0] + u[0] * s, e[1] + u[1] * s)
                    c = near(q, pred, 3500.0)
                    if not c:
                        break
                    al = (q[0] - c[0]) * c[3] + (q[1] - c[1]) * c[4]
                    cx, cy = c[0] + c[3] * al, c[1] + c[4] * al
                    n = (-c[4], c[3])
                    if (q[0] - cx) * n[0] + (q[1] - cy) * n[1] < 0:
                        n = (-n[0], -n[1])
                    w = w0 * (1 - _smooth(0.3 * MERGE_CM, MERGE_CM, max(0.0, s)))
                    rows.append([round(cx + n[0] * half), round(cy + n[1] * half), round(n[0], 4), round(n[1], 4), round(w), round(c[2])])
                if len(rows) > 2 and w0 > 50:
                    gores.append({"deck": c0[5] == "SM_Elev_Deck20m", "rows": rows})
                    done.append(f"{slug}:{p['i']}")
            else:
                c = near(e, lambda s: s[5] != RAMP and abs(s[3] * u[0] + s[4] * u[1]) < 0.5, 4500.0)
                if not c:
                    continue
                t = (c[3], c[4])
                ex = (-t[1], t[0])
                if (c[0] - e[0]) * ex[0] + (c[1] - e[1]) * ex[1] < 0:
                    ex = (-ex[0], -ex[1])
                D = (c[0] - e[0]) * ex[0] + (c[1] - e[1]) * ex[1] - c[6]["carriageway"]
                if D < 300:
                    continue
                flares.append([round(e[0]), round(e[1]), round(ex[0], 4), round(ex[1], 4), round(t[0], 4), round(t[1], 4),
                               round(D), round(min(KERB_R, D - 50)), c[6]["sidewalk"], round(p["z"]), round(c[2])])
                done.append(f"{slug}:{p['i']}")
    for p, q in ends:
        l = math.hypot(p["x"] - q["x"], p["y"] - q["y"])
        u = ((p["x"] - q["x"]) / l, (p["y"] - q["y"]) / l)
        e = (p["x"], p["y"])
        c = near(e, lambda s: s[5] != RAMP and s[5] not in FREEWAY and abs(s[3] * u[0] + s[4] * u[1]) < 0.5, 1500.0)
        if not c:
            continue  # a cul-de-sac, or a street cut short of a ramp
        t = (c[3], c[4])
        ex = (-t[1], t[0])
        if ex[0] * u[0] + ex[1] * u[1] < 0:  # the way the street arrives (its end may lie on the other's centre line)
            ex = (-ex[0], -ex[1])
        D = (c[0] - e[0]) * ex[0] + (c[1] - e[1]) * ex[1] - c[6]["carriageway"]
        # the mouth starts just behind the street's sidewalk, wherever the spline itself stops (most run on to the kerb)
        back = max(0.0, max(450.0, c[6]["sidewalk"] + 100.0) - D)
        flares.append([round(e[0] - ex[0] * back), round(e[1] - ex[1] * back), round(ex[0], 4), round(ex[1], 4),
                       round(t[0], 4), round(t[1], 4), round(D + back), round(min(PRIVATE_R, D + back - 50)),
                       c[6]["sidewalk"], round(p["z"]), round(c[2]), PRIVATE["half"]])
    return {"gores": gores, "flares": flares, "done": done}


def _demo() -> None:
    def pt(i, x, y, z=0.0, yaw=0.0, mesh=""):
        return {"i": i, "x": x, "y": y, "z": z, "pitch": 0.0, "yaw": yaw, "mesh": mesh}

    def seg(a, b, mesh, L):
        return {"a": a, "b": b, "ta": L, "tb": -L, "mesh": mesh}
    # a viaduct along +X at 7.5 m, a ramp arriving beside it; an avenue along +Y, a ramp meeting it head on
    fw = ([pt(k, k * 2000.0, 0.0, 750.0) for k in range(12)], [seg(k, k + 1, "Elevated/SM_Elev_Deck20m", 2000.0) for k in range(11)])
    ramps = ([pt(0, 2000.0, 2600.0, 750.0), pt(1, 4000.0, 2600.0, 750.0, mesh=GORE),
              pt(2, 44000.0, 5000.0), pt(3, 47500.0, 5000.0, mesh=FLARE)],
             [seg(0, 1, "Elevated/" + RAMP, 2000.0), seg(2, 3, "Elevated/" + RAMP, 3500.0)])
    av = ([pt(k, 50000.0, k * 2000.0, 0.0, 90.0) for k in range(6)], [seg(k, k + 1, "SM_Road_FourLane_Straight10m", 2000.0) for k in range(5)])
    got = joins({"fw": fw, "ramps": ramps, "av": av})
    assert got["done"] == ["ramps:1", "ramps:3"], got["done"]
    rows = got["gores"][0]["rows"]
    assert got["gores"][0]["deck"] and all(r[1] == 1650 and r[5] == 750 for r in rows), rows[:2]
    assert rows[1][4] == 1600 and rows[-1][4] == 0 and all(a[4] >= b[4] for a, b in zip(rows, rows[1:])), [r[4] for r in rows]
    f = got["flares"][0]
    assert f[2:4] == [1.0, 0.0] and f[6] == 2500 - 1050 and f[8] == 350.0, f  # 25 m to the centre line, less the carriageway
    # a private street running into the avenue's kerb, and a stub leaving it side on: both get an 8 m mouth
    acc = ([pt(0, 40000.0, 3000.0), pt(1, 49500.0, 3000.0), pt(2, 45000.0, 3000.0), pt(3, 45000.0, 9000.0)],
           [seg(0, 1, "SM_Path_PrivateStreet_10m", 9500.0), {"a": 2, "b": 3, "ta": 0.0, "tb": 0.0, "mesh": "SM_Path_PrivateStreet_10m"}])
    pf = joins({"av": av}, {"acc": acc})["flares"]
    assert [48500, 3000, 1.0, 0.0, 0.0, 1.0, 450, 400, 350.0, 0, 0, 400.0] in pf, pf
    assert [45000, 3850, 0.0, -1.0, 1.0, 0.0, 450, 400, 0, 0, 0, 400.0] in pf and len(pf) == 2, pf
    print("ramp joins ok", len(rows), "rows in a merge lane")


if __name__ == "__main__":
    _demo()

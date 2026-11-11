"""Airports: a complete procedural system, from a grass-strip airfield to an international hub.

AirportGenerator (planned at world level, before the cities' streets, like freeways and shipyards)
  AirportSitePlanner (`plan_world`): per city, the airport type from its population; candidate sites on a ring
      outside the city, runways turned into the prevailing wind (option wind_deg, +-15 degrees to fit the land);
      each scored by grading (how far the ground strays from level over the airfield), no water under the
      airfield, no keep zone, no other city, inside the map. The airport's land is reserved: city streets and
      lots inside it are removed, and its noise zone (an ellipse round the runways) turns housing land industrial.
  Layout (`build`), in the airport's frame: u along the runways, v across them (+v toward the second runway,
      -v toward the terminal and the city side):
      RunwayGenerator    runways (R(s) = P0 + s D) as RW / RS spline edges, split at every connector, thresholds,
                         edge lights every 60 m, approach light bars
      TaxiwayGenerator   a parallel taxiway, end connectors at 90 degrees, rapid exits at 30 degrees, holding
                         positions on every connector: one connected taxiway graph (TW spline edges, junction
                         pieces where they meet)
      ApronGenerator     apron slabs beside the parallel taxiway; stands are placed FIRST, the terminal after,
                         so no gate is where an aircraft cannot park
      TerminalGenerator  linear (regional: gates along one face), pier / finger (international: piers into the
                         apron, gates both sides), small terminal / FBO (general aviation)
      AircraftStandGenerator  aircraft on every stand, jet bridges from the gate to the door
      CargoAirportGenerator   cargo apron, freight terminal, sorting warehouse, truck yard, its own truck road
      HangarGenerator    maintenance hangars, paint hangar, engine shop, small T-hangars (GA), shelters (military)
      AirfieldEquipmentGenerator  control tower, radar, windsock, runway / approach lights, hold signs
      AirportRoadGenerator  a two-level terminal curb (departures on a deck above, arrivals at grade), parking
                         garages, surface lots, rental cars, hotel, access road to the city; cargo trucks on a
                         separate road
      AirportUtilityGenerator / SupportGenerator  fuel farm, substation, fire and rescue (ARFF) at midfield on
                         the taxiway, catering, ground support depot, operations / administration
  AirportValidationSystem (`validate`): every runway reached by >= 2 connectors, every stand on an apron that
      meets the parallel taxiway, nothing tall in a runway's object-free area or approach, cargo trucks never
      on the passenger curb, the landside road reaches the city, no water under a runway.

Dimensions are game-scale approximations of FAA AC 150/5300-13B (airfield) and 150/5360-13A (terminals), not
certified designs. Rows: [x, y, z, yaw, sx, sy, sz, kind]; buildings: lot rows [x, y, yaw, w, d, fx, fy, role, True].
"""

from __future__ import annotations

import math

# type: runway length, kind (RW wide / RS small), runways, spacing, parallel taxiway offset, apron depth,
# gates, terminal layout, cargo, people needed
TYPES = {
    "general_aviation": dict(L=90000.0, kind="RS", runways=1, S=0.0, T=6000.0, apron=7000.0, gates=0,
                             terminal="fbo", cargo=False, min_pop=2000),
    "regional": dict(L=180000.0, kind="RW", runways=1, S=0.0, T=12000.0, apron=9000.0, gates=8,
                     terminal="linear", cargo=True, min_pop=20000),
    "international": dict(L=300000.0, kind="RW", runways=2, S=45000.0, T=15000.0, apron=22000.0, gates=24,
                          terminal="pier", cargo=True, min_pop=120000),
    "cargo": dict(L=250000.0, kind="RW", runways=1, S=0.0, T=14000.0, apron=14000.0, gates=0,
                  terminal="none", cargo=True, min_pop=60000),
    "military": dict(L=250000.0, kind="RW", runways=1, S=0.0, T=14000.0, apron=12000.0, gates=0,
                     terminal="none", cargo=False, min_pop=0),
}
RSA = 30000.0  # runway safety area / approach margin past each end
LANDSIDE = 30000.0  # terminal + curb + parking depth on the city side
RW_HALF = {"RW": 2250.0, "RS": 900.0}
OFA = {"RW": 7500.0, "RS": 3500.0}  # object-free half width from the runway centre line
KINDS = {
    "apron": ("SM_Kit_Apron", [1, 1, 0.1], "mix:fg-dim:bg:0.3", "Aprons"),
    "plane": ("SM_Air_Airliner", [40, 36, 12], "fg", "Aircraft"),
    "small_plane": ("SM_Air_SmallPlane", [9, 11, 3], "mix:fg:blue:0.2", "Light aircraft"),
    "jet_bridge": ("SM_Air_JetBridge", [30, 4, 6], "mix:fg-dim:bg:0.2", "Jet bridges"),
    "hangar": ("SM_Air_Hangar", [60, 50, 22], "mix:fg-dim:blue:0.2", "Hangars"),
    "shelter": ("SM_Air_Shelter", [30, 40, 12], "mix:fg-dim:green:0.3", "Aircraft shelters"),
    "atc_tower": ("SM_Air_ControlTower", [12, 12, 45], "fg", "Control towers"),
    "radar": ("SM_Air_Radar", [8, 8, 14], "mix:fg:bg:0.3", "Radar"),
    "windsock": ("SM_Air_Windsock", [1, 4, 6], "amber", "Windsocks"),
    "edge_light": ("SM_Air_EdgeLight", [0.4, 0.4, 0.5], "yellow", "Runway lights"),
    "approach_light": ("SM_Air_ApproachBar", [1, 30, 2], "yellow", "Approach lights"),
    "threshold": ("SM_Air_Threshold", [30, 45, 0.05], "fg", "Runway thresholds"),
    "hold_sign": ("SM_Air_HoldSign", [0.4, 3, 1.5], "red", "Holding positions"),
    "gse": ("SM_Air_GSE", [6, 3, 2.5], "mix:amber:fg:0.4", "Ground support vehicles"),
    "fuel_tank": ("SM_Kit_FuelTank", [20, 20, 12], "mix:fg:bg:0.2", "Fuel farms"),
    "uld": ("SM_Kit_ContainerStack", [12.2, 2.5, 7.8], "mix:blue:fg-dim:0.4", "Cargo containers"),
    "fence": ("SM_Kit_Fence", [1, 0.1, 2.5], "fg-dim", "Security fences"),
}
TALL = ("hangar", "shelter", "atc_tower", "radar", "fuel_tank", "jet_bridge", "plane", "uld")


class Frame:
    """The airport's frame: origin at its centre, u along the runways, v across them."""

    def __init__(self, cx, cy, deg):
        self.cx, self.cy = cx, cy
        self.ux, self.uy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        self.vx, self.vy = -self.uy, self.ux
        self.deg = deg

    def w(self, u, v):
        return (self.cx + self.ux * u + self.vx * v, self.cy + self.uy * u + self.vy * v)

    def uv(self, x, y):
        dx, dy = x - self.cx, y - self.cy
        return dx * self.ux + dy * self.uy, dx * self.vx + dy * self.vy

    def yaw(self, du, dv):
        return math.degrees(math.atan2(self.uy * du + self.vy * dv, self.ux * du + self.vx * dv))


def extent(spec) -> tuple:
    """(u half length, v from, v to) of an airport's land."""
    u = spec["L"] / 2 + RSA
    v0 = -(spec["T"] + 3000 + spec["apron"] + (LANDSIDE if spec["terminal"] in ("linear", "pier") else 15000))
    if spec["terminal"] == "pier":
        v0 -= 8000
    v1 = spec["S"] * (spec["runways"] - 1) + OFA[spec["kind"]] + 2000
    return u, v0, v1


def inside(ap: dict, x: float, y: float, margin: float = 0.0) -> bool:
    f = Frame(*ap["center"], ap["heading"])
    u, v = f.uv(x, y)
    hu, v0, v1 = extent(TYPES[ap["type"]])
    return abs(u) <= hu + margin and v0 - margin <= v <= v1 + margin


def in_noise(ap: dict, x: float, y: float) -> bool:
    """The noise zone (a game stand-in for the 65 DNL contour): an ellipse round the runways."""
    f = Frame(*ap["center"], ap["heading"])
    u, v = f.uv(x, y)
    spec = TYPES[ap["type"]]
    a = spec["L"] / 2 + (100000 if spec["kind"] == "RW" else 30000)
    b = (40000 if spec["kind"] == "RW" else 15000) + spec["S"] * (spec["runways"] - 1) / 2
    vc = spec["S"] * (spec["runways"] - 1) / 2
    return (u / a) ** 2 + ((v - vc) / b) ** 2 <= 1.0


def pick_type(pop: int, opts: dict | None = None) -> str | None:
    o = opts or {}
    if o.get("airports", "auto") == "none":
        return None
    for t in ("international", "regional", "general_aviation"):
        if pop >= TYPES[t]["min_pop"]:
            return t
    return None


def plan_world(cities: list, ground, wet, keep: list | None, bounds: tuple, opts: dict | None = None,
               populations: dict | None = None) -> list:
    """AirportSitePlanner: at most one airport per city (one international per world), plus a military base
    when asked. Returns airports [{city, type, center, heading, score, grading_m}]."""
    o = opts or {}
    wind = float(o.get("wind_deg", 240.0))
    out = []
    intl = False
    order = sorted(cities, key=lambda c: -(populations or {}).get(c["name"], 0))
    wanted = []
    for c in order:
        pop = (populations or {}).get(c["name"], 0)
        own = (c.get("features") or {}).get("airports")  # the city's own choice beats the world's
        t = pick_type(pop, {**o, "airports": own or o.get("airports", "auto")})
        if own == "auto" and not t:
            t = "general_aviation"  # asked for one: even a small town gets an airfield
        if t == "international" and intl:
            t = "regional"
        if t == "international":
            intl = True
        if t:
            wanted.append((c, t))
    if o.get("military_base") == "yes" and order:
        wanted.append((order[-1], "military"))
    (x0, y0), (x1, y1) = bounds
    for c, t0 in wanted:
        # no room for this size: a smaller airport (an international city may still fit a regional one)
        ladder = {"international": ["international", "regional", "general_aviation"],
                  "regional": ["regional", "general_aviation"]}.get(t0, [t0])
        for t in ladder:
            best = _site(c, t, cities, ground, wet, keep, (x0, y0, x1, y1), wind, out)
            if best:
                out.append({**best, "wanted": t0} if t != t0 else best)
                break
    return out


def _site(c, t, cities, ground, wet, keep, box, wind, out):
    """The best site for an airport of type t near city c (three rings of 24 bearings, three headings), or None."""
    x0, y0, x1, y1 = box
    spec = TYPES[t]
    hu, v0, v1 = extent(spec)
    best = None
    for heading in (wind - 15, wind, wind + 15):
        for dist in (1.0, 1.25, 1.6):
            for k in range(24):
                ang = k * math.pi / 12
                reach = (c["radius"] * 1.1 + math.hypot(hu, (v1 - v0) / 2) * 0.7 + 5000) * dist
                cx, cy = c["x"] + math.cos(ang) * reach, c["y"] + math.sin(ang) * reach
                f = Frame(cx, cy, heading)
                if (c["x"] - cx) * f.vx + (c["y"] - cy) * f.vy > 0:  # the terminal side (-v) faces the city
                    f = Frame(cx, cy, heading + 180)
                cand = {"city": c["name"], "type": t, "center": [round(cx), round(cy)], "heading": round(f.deg % 360, 1)}
                corners = [f.w(a * hu, v) for a in (-1, 1) for v in (v0, v1)]
                if any(not (x0 <= x <= x1 and y0 <= y <= y1) for x, y in corners):
                    continue
                if any(inside(a, *p, 2000) for a in out for p in corners) or \
                        any(inside(cand, a["center"][0], a["center"][1], 20000) for a in out):
                    continue
                pts = [f.w(i * hu / 6, v0 + (v1 - v0) * j / 4) for i in range(-6, 7) for j in range(5)]
                if any(math.hypot(o2["x"] - x, o2["y"] - y) < o2["radius"] * 1.02 for o2 in cities for x, y in pts):
                    continue  # on a city
                if keep and any(k2["x0"] - 2000 < x < k2["x1"] + 2000 and k2["y0"] - 2000 < y < k2["y1"] + 2000
                                for k2 in keep for x, y in pts + corners):
                    continue
                if wet and any(wet(x, y) for x, y in pts):
                    continue  # no airfield on water
                zs = [z for z in (ground(x, y) for x, y in pts) if z is not None]
                if len(zs) < 20:
                    continue
                mean = sum(zs) / len(zs)
                grading = sum(abs(z - mean) for z in zs) / len(zs)
                score = grading + 0.02 * reach + abs(((heading - wind + 180) % 360) - 180) * 20
                if best is None or score < best[0]:
                    best = (score, {**cand, "grading_m": round(grading / 100, 1), "level_cm": round(mean)})
    return best[1] if best else None


def build(ap: dict, ground, city_nodes: dict | None = None, wet=None) -> dict:
    """The airport's airside graph, landside roads, rows, buildings, parking and report."""
    from .neighbourhoods import parking
    spec = TYPES[ap["type"]]
    f = Frame(*ap["center"], ap["heading"])
    L, T, A = spec["L"], spec["T"], spec["apron"]
    kind = spec["kind"]
    tag = ("ap", ap["city"])
    nodes, edges, roads_n, roads_e = {}, [], {}, []
    rows, lots, park_slabs, stalls = [], [], [], []
    level = ap.get("level_cm")
    zg = lambda x, y: (ground(x, y) if ground(x, y) is not None else 0.0) if level is None else level

    def node(key, u, v, store=nodes):
        x, y = f.w(u, v)
        store[tag + key] = [round(x), round(y)]
        return tag + key

    def row(u, v, kind_, sx=1.0, sy=1.0, sz=1.0, yaw=None, z=None):
        x, y = f.w(u, v)
        rows.append([round(x), round(y), round(zg(x, y) if z is None else z), round(f.deg if yaw is None else yaw, 1),
                     round(sx, 2), round(sy, 2), round(sz, 2), kind_])

    def slab(u0, u1, v0, v1, kind_="apron"):
        row((u0 + u1) / 2, (v0 + v1) / 2, kind_, (u1 - u0) / 100, (v1 - v0) / 100)

    def building(u, v, w_u, d_v, role, name, face_v=-1):
        x, y = f.w(u, v)
        fx, fy = f.w(u, v + face_v * d_v / 2)
        lots.append([round(x), round(y), round(f.yaw(0, face_v), 1), round(w_u), round(d_v), round(fx), round(fy),
                     role, True])
        built.append({"what": name, "x": round(x), "y": round(y)})

    built: list = []
    checks: list = []
    # ---- runways and taxiways (one connected airside graph)
    exits = [-L / 2, -L / 6, L / 6, L / 2] if L >= 150000 else [-L / 2, 0.0, L / 2]
    for r in range(spec["runways"]):
        vr = r * spec["S"]
        rn = [node(("rw", r, k), u, vr) for k, u in enumerate(exits)]
        for a, b in zip(rn, rn[1:]):
            edges.append([a, b, kind])
        # thresholds, edge lights, approach lights
        for end in (-1, 1):
            row(end * (L / 2 - 1500), vr, "threshold", yaw=f.deg)
            for k in range(10):
                row(end * (L / 2 + 3000 + k * 3000), vr, "approach_light", yaw=f.deg)
        for k in range(int(L // 6000) + 1):
            for side in (-1, 1):
                row(-L / 2 + k * 6000, vr + side * (RW_HALF[kind] + 150), "edge_light")
        # its parallel taxiway (runway 0: on the terminal side; runway 1: between the runways)
        vt = -T if r == 0 else vr - spec["S"] / 2
        tn = [node(("tw", r, k), u, vt) for k, u in enumerate(exits)]
        for a, b in zip(tn, tn[1:]):
            edges.append([a, b, "TW"])
        for k, u in enumerate(exits):  # 90 degree connectors at the ends, holding positions on each
            if k in (0, len(exits) - 1) or r == 1:
                edges.append([tn[k], rn[k], "TW"])
                hold = vr - math.copysign(RW_HALF[kind] + 6000, vr - vt)
                row(u + 1500, hold, "hold_sign", yaw=f.deg)
            elif L >= 150000:  # rapid exits at 30 degrees: leave the runway ahead of the connector node
                du = abs(vr - vt) / math.tan(math.radians(30))
                sgn = 1 if u > 0 else -1
                xn = node(("rx", r, k), u - sgn * du, vr)
                edges.append([xn, tn[k], "TW"])
                # split the runway at the exit so the taxiway joins it
                for e in edges:
                    if e[2] == kind and {e[0], e[1]} == {rn[k], rn[k - 1] if sgn > 0 else rn[k + 1]}:
                        edges.remove(e)
                        edges += [[e[0], xn, kind], [xn, e[1], kind]]
                        break
                row(u - sgn * du * 0.6, vr - math.copysign(RW_HALF[kind] + 6000, vr - vt), "hold_sign", yaw=f.deg)
            else:
                edges.append([tn[k], rn[k], "TW"])
        if r == 1:  # the two parallel taxiways join at both ends
            for k in (0, len(exits) - 1):
                edges.append([tn[k], tag + ("tw", 0, k), "TW"])
        checks.append(("runway connectors", sum(1 for e in edges if e[2] == "TW" and (e[0] in rn or e[1] in rn)) >= 2,
                       f"runway {r + 1}"))
    # ---- apron: stands first, then the terminal round them
    ap_v0, ap_v1 = -T - 3000 - A, -T - 3000  # apron band (v)
    gates = spec["gates"]
    terminal_front = ap_v0
    plane_rows = []
    if spec["terminal"] == "linear":
        span = gates * 5000
        slab(-span / 2 - 4000, span / 2 + 4000, ap_v0, ap_v1)
        for g in range(gates):
            u = -span / 2 + (g + 0.5) * 5000
            row(u, ap_v0 + A * 0.55, "plane", 0.9, 0.9, 0.9, yaw=f.yaw(0, -1))  # nose to the terminal
            plane_rows.append((u, ap_v0 + A * 0.55))
            row(u - 300, ap_v0 + 1500, "jet_bridge", yaw=f.yaw(0, 1))
            row(u + 1800, ap_v0 + A * 0.3, "gse")
        building(0, ap_v0 - 2500, span + 4000, 5000, "transit", "passenger terminal (linear, check-in, security, "
                                                              "baggage, gates)")
        terminal_front = ap_v0 - 5000
    elif spec["terminal"] == "pier":
        piers = 2 if gates <= 30 else 3
        per = gates // piers
        P = (per // 2) * 6000 + 4000  # pier length (gates both sides, 60 m apart)
        width = piers * 22000 + 8000
        slab(-width / 2, width / 2, ap_v0 - P, ap_v1)
        for p in range(piers):
            up = -width / 2 + 8000 + p * 22000 + 7000
            building(up, ap_v0 - P / 2 + 2000, 2500, P, "transit", f"concourse pier {chr(65 + p)} (gates, lounges, "
                                                                     f"shops)", face_v=1)
            for g in range(per):
                side = -1 if g % 2 == 0 else 1
                v = ap_v0 - P + 4000 + (g // 2) * 6000
                row(up + side * 6000, v, "plane", yaw=f.yaw(-side, 0))
                plane_rows.append((up + side * 6000, v))
                row(up + side * 2400, v, "jet_bridge", yaw=f.yaw(side, 0))
                row(up + side * 6000, v + 2500, "gse")
        building(0, ap_v0 - P - 3500, width - 4000, 7000, "transit", "main terminal (departures / arrivals halls, "
                                                                    "check-in, security, immigration, baggage claim)")
        terminal_front = ap_v0 - P - 7000
        ap_v0 -= P
    elif spec["terminal"] == "fbo":
        slab(-12000, 12000, ap_v0, ap_v1)
        for k in range(8):  # light aircraft tied down on the apron
            row(-10000 + k * 2800, ap_v0 + A * 0.5, "small_plane", yaw=f.yaw(0, -1))
            plane_rows.append((-10000 + k * 2800, ap_v0 + A * 0.5))
        building(0, ap_v0 - 1500, 2400, 1800, "civic", "FBO / pilot terminal")
        for k in range(6):  # T-hangars
            row(14000 + k * 2200, ap_v0 + A * 0.5, "hangar", 0.3, 0.3, 0.3, yaw=f.yaw(0, 1))
        terminal_front = ap_v0 - 3000
    else:  # cargo / military apron
        slab(-30000, 30000, ap_v0, ap_v1)
    # ---- cargo district (its own apron, terminal, truck yard and road)
    cargo_u = L / 2 - 30000
    if spec["cargo"]:
        slab(cargo_u - 20000, cargo_u + 20000, ap_v0, ap_v1)
        n_cp = 4 if ap["type"] in ("international", "cargo") else 2
        for k in range(n_cp):
            row(cargo_u - 15000 + k * 10000, (ap_v0 + ap_v1) / 2, "plane", 1.15, 1.15, 1.15, yaw=f.yaw(0, -1))
            plane_rows.append((cargo_u - 15000 + k * 10000, (ap_v0 + ap_v1) / 2))
        building(cargo_u, ap_v0 - 3500, 30000, 6000, "warehouse", "freight terminal and sorting centre")
        building(cargo_u + 22000, ap_v0 - 3000, 8000, 5000, "warehouse", "cold storage and customs")
        slab(cargo_u - 15000, cargo_u + 15000, ap_v0 - 12000, ap_v0 - 6500, "apron")  # truck yard
        for k in range(10):
            row(cargo_u - 12000 + k * 2600, ap_v0 - 9000, "uld", yaw=f.yaw(0, 1))
    # ---- maintenance, support, equipment
    mro_u = -L / 2 + 30000
    if ap["type"] != "general_aviation":
        for k in range(3 if ap["type"] != "military" else 2):
            row(mro_u + k * 7500, ap_v0 - 3000, "hangar", yaw=f.yaw(0, 1))  # doors to the airside
        building(mro_u + 24000, ap_v0 - 3000, 5000, 4000, "factory", "engine and component workshops")
        building(mro_u + 32000, ap_v0 - 3000, 6000, 4000, "warehouse", "ground support equipment depot")
        for k in range(6):
            row(mro_u + 29000 + k * 900, ap_v0 - 6000, "gse")
    if ap["type"] == "military":
        for k in range(10):
            row(-L / 2 + 45000 + k * 4500, ap_v0 - 2000, "shelter", yaw=f.yaw(0, 1))
        building(10000, ap_v0 - 8000, 6000, 4000, "civic", "base command and operations")
        hu, v0, v1 = extent(spec)
        for k in range(int(2 * hu // 1000)):
            row(-hu + (k + 0.5) * 1000, v0, "fence", 10, 1, 1)
            row(-hu + (k + 0.5) * 1000, v1, "fence", 10, 1, 1)
    # fire and rescue at midfield on the parallel taxiway, tower beside the apron, radar landside
    building(L * 0.12, -T - 4000, 3500, 2500, "fire_station", "aircraft rescue and firefighting station", face_v=1)
    if ap["type"] != "general_aviation":
        row(-L * 0.18, ap_v0 - 3000, "atc_tower")
        row(L / 2 - 15000, ap_v0 - 14000, "radar")
        building(-L * 0.18 - 6000, ap_v0 - 4000, 4000, 3000, "civic", "airport operations centre")
        building(-L * 0.25, ap_v0 - 4000, 4000, 3000, "factory", "flight catering")
    row(0, OFA[kind] - 1000 + spec["S"] * (spec["runways"] - 1), "windsock")
    fuel_u = -L / 2 + 12000
    for k in range(4 if ap["type"] != "general_aviation" else 1):
        row(fuel_u + (k % 2) * 2600, ap_v0 - 12000 - (k // 2) * 2600, "fuel_tank", 0.5 if ap["type"] == "general_aviation" else 1, 1, 1)
    building(fuel_u + 7000, ap_v0 - 12000, 2000, 1500, "utility", "airport substation")
    # ---- landside: two-level terminal curb, parking, rental cars, hotel, access; cargo road apart
    access_from = None
    if spec["terminal"] in ("linear", "pier", "fbo"):
        tf = terminal_front
        w = 20000 if spec["terminal"] == "fbo" else (gates * 5000 + 10000 if spec["terminal"] == "linear" else 60000)
        dep = [node(("dep", k), u, tf - 2000, roads_n) for k, u in enumerate((-w / 2, w / 2))]
        arr = [node(("arr", k), u, tf - 4500, roads_n) for k, u in enumerate((-w / 2 - 3000, w / 2 + 3000))]
        if spec["terminal"] != "fbo":  # departures upstairs on a deck, arrivals at grade
            x0, y0 = roads_n[dep[0]]
            roads_n[dep[0]] = [x0, y0, 600]
            x1, y1 = roads_n[dep[1]]
            roads_n[dep[1]] = [x1, y1, 600]
            roads_e.append([dep[0], dep[1], "ED"])
        else:
            roads_e.append([dep[0], dep[1], "2S"])
        roads_e.append([arr[0], arr[1], "4L"])
        back = [node(("loop", k), u, tf - 16000, roads_n) for k, u in enumerate((-w / 2 - 3000, w / 2 + 3000))]
        roads_e += [[arr[0], back[0], "4L"], [arr[1], back[1], "4L"], [back[0], back[1], "4L"],
                    [dep[0], back[0], "ER"], [dep[1], back[1], "ER"]]
        access_from = back[0]
        if spec["terminal"] != "fbo":
            building(-w / 4, tf - 9000, 9000, 6000, "parking_garage", "parking garage")
            building(w / 4, tf - 9000, 9000, 6000, "parking_garage", "parking garage")
            building(w / 2 + 9000, tf - 9000, 4000, 4000, "shop", "rental car centre")
            building(-w / 2 - 9000, tf - 10000, 4000, 3500, "office", "airport hotel")
            if ap["type"] == "international":
                building(0, tf - 22000, 8000, 3000, "transit", "airport train and bus station")
        lot_f = type("F", (), {"w": staticmethod(lambda u, v: f.w(u, v)), "yaw": staticmethod(lambda du, dv: f.yaw(du, dv))})
        s0, st0 = parking(lot_f, -w / 2, w / 2, tf - 28000, tf - 17500)
        park_slabs.append(s0)
        stalls += st0
    # access road to the nearest city street; cargo trucks get their own road
    def nearest_city(x, y):
        if not city_nodes:
            return None
        return min(city_nodes.items(), key=lambda kv: math.hypot(kv[1][0] - x, kv[1][1] - y))

    links = []
    for name, start in (("access", access_from), ("cargo", None)):
        if name == "cargo":
            if not spec["cargo"]:
                continue
            start = node(("cargo_gate",), cargo_u, ap_v0 - 15000, roads_n)
            roads_e.append([start, node(("cargo_yard",), cargo_u, ap_v0 - 9000, roads_n), "4L"])
        if start is None:
            start = node(("gate",), 0, ap_v0 - 6000, roads_n)
        sx, sy = roads_n[start][:2]
        hit = nearest_city(sx, sy)
        if hit:
            end = tag + (name, "city")
            roads_n[end] = list(hit[1][:2])
            roads_e.append([start, end, "4L"])
            links.append({"road": name, "to_city_node": list(hit[1][:2]),
                          "length_m": round(math.dist((sx, sy), hit[1][:2]) / 100)})
    report = validate(ap, f, spec, nodes, edges, rows, lots, plane_rows, (ap_v0, ap_v1), roads_n, roads_e, links,
                      checks, ground, wet)
    noise = {"a_m": round((spec["L"] / 2 + (100000 if kind == "RW" else 30000)) / 100),
             "b_m": round(((40000 if kind == "RW" else 15000) + spec["S"] * (spec["runways"] - 1) / 2) / 100)}
    return {"airside": (nodes, edges), "landside": (roads_n, roads_e), "rows": rows, "lots": lots,
            "parking": park_slabs, "stalls": stalls, "facilities": built, "links": links, "noise_zone": noise,
            "report": report}


def validate(ap, f, spec, nodes, edges, rows, lots, planes, apron_v, roads_n, roads_e, links, checks, ground, wet):
    """AirportValidationSystem."""
    bad = [d for what, ok, d in checks if not ok]
    kind = spec["kind"]
    # one connected airside graph
    adj: dict = {}
    for a, b, *_ in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    start = next(iter(adj), None)
    seen, todo = set(), [start] if start else []
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo += list(adj[n])
    if len(seen) != len(adj):
        bad.append(f"the taxiway graph is in {len(adj) - len(seen) + 1}+ pieces")
    # stands reachable: every aircraft on an apron that touches the parallel taxiway band
    v0, v1 = apron_v
    for u, v in planes:
        if not (v0 - 1 <= v <= v1 + 1):
            bad.append(f"stand at u={round(u / 100)} m is off the apron")
    if v1 < -spec["T"] - 3500:
        bad.append("the apron does not meet the parallel taxiway")
    # object-free area and approaches: nothing tall near a runway or in line with its ends
    for r in range(spec["runways"]):
        vr = r * spec["S"]
        for x, y, *_rest in [(row[0], row[1], row[7]) for row in rows if row[7] in TALL] + \
                            [(lt[0], lt[1], "building") for lt in lots]:
            u, v = f.uv(x, y)
            if abs(u) <= spec["L"] / 2 + RSA and abs(v - vr) < OFA[kind]:
                bad.append(f"{_rest[0]} in runway {r + 1}'s object-free area at u={round(u / 100)} m")
    # no water under a runway
    if wet:
        for r in range(spec["runways"]):
            for k in range(21):
                x, y = f.w(-spec["L"] / 2 + spec["L"] * k / 20, r * spec["S"])
                if wet(x, y):
                    bad.append(f"water under runway {r + 1}")
                    break
    # cargo trucks never use the passenger curb; the landside reaches the city
    curb = {n for n in roads_n if n[2] in ("dep", "arr")}
    cargo_nodes = {n for n in roads_n if n[2] in ("cargo_gate", "cargo_yard")}
    for a, b, *_ in roads_e:
        if (a in cargo_nodes and b in curb) or (b in cargo_nodes and a in curb):
            bad.append("cargo trucks share the passenger curb")
    if spec["terminal"] in ("linear", "pier", "fbo") and not any(l["road"] == "access" for l in links):
        bad.append("no access road to the city")
    # runway grade the landscape spline has to take out (information: the spline grades it)
    zs = []
    for k in range(21):
        x, y = f.w(-spec["L"] / 2 + spec["L"] * k / 20, 0)
        z = ground(x, y)
        if z is not None:
            zs.append(z)
    relief = (max(zs) - min(zs)) / 100 if zs else 0.0
    return {"ok": not bad, "problems": bad[:30], "runways": spec["runways"], "stands": len(planes),
            "taxiway_edges": sum(1 for e in edges if e[2] == "TW"), "runway_relief_m": round(relief, 1)}


def layers(airports_rows: list) -> dict:
    """Built-in PCG site layers for the airports' rows."""
    by: dict = {}
    for r in airports_rows:
        by.setdefault(r[7], []).append(r[:7])
    out = {}
    for kind, rows in by.items():
        mesh, box, tint, name = KINDS[kind]
        out[name] = {"kind": "site", "role": "site", "mesh": mesh, "box_m": box, "tint": tint, "count": len(rows),
                     "shown": len(rows), "points": rows, "meshes": [], "row": "x, y, z, yaw, sx, sy, sz"}
    return out


def _demo() -> None:
    flat = lambda x, y: 500.0
    lake = lambda x, y: 300000 < x < 400000
    city = {"name": "Metro", "x": 0, "y": 0, "radius": 150000}
    bounds = ((-1000000, -1000000), (1000000, 1000000))
    for pop, want in ((300000, "international"), (40000, "regional"), (5000, "general_aviation")):
        aps = plan_world([city], flat, lake, [], bounds, {"wind_deg": 240}, {"Metro": pop})
        assert aps and aps[0]["type"] == want, (pop, aps)
        ap = aps[0]
        assert abs(((ap["heading"] - 240 + 90) % 180) - 90) <= 15.01, "runways into the wind"
        hu, v0, v1 = extent(TYPES[want])
        f = Frame(*ap["center"], ap["heading"])
        assert all(math.hypot(x, y) > city["radius"] for x, y in (f.w(a * hu, v) for a in (-1, 1) for v in (v0, v1))), \
            "airport land outside the city"
        assert not any(lake(*f.w(a * hu, v)) for a in (-1, 0, 1) for v in (v0, 0, v1)), "no water on the airfield"
        nodes = {(i, j): [i * 10000, j * 10000] for i in range(-15, 16) for j in range(-15, 16)}
        got = build(ap, flat, nodes, lake)
        rep = got["report"]
        assert rep["ok"], (want, rep)
        kinds = {r[7] for r in got["rows"]}
        assert {"threshold", "edge_light", "approach_light", "hold_sign", "windsock"} <= kinds, (want, kinds)
        n, e = got["airside"]
        assert {"TW"} <= {k for *_x, k in e} and (TYPES[want]["kind"] in {k for *_x, k in e}), "runways + taxiways"
        roles = {lt[7] for lt in got["lots"]}
        if want == "international":
            assert rep["runways"] == 2 and rep["stands"] >= 24 + 4, rep
            assert {"jet_bridge", "plane", "atc_tower", "radar", "hangar", "uld", "gse", "fuel_tank"} <= kinds, kinds
            assert {"transit", "warehouse", "fire_station", "parking_garage", "office"} <= roles, roles
            rn, re_ = got["landside"]
            assert any(k == "ED" for *_x, k in re_), "departures on the upper level"
            assert {l["road"] for l in got["links"]} == {"access", "cargo"}, got["links"]
        if want == "general_aviation":
            assert "small_plane" in kinds and "civic" in roles
        # noise: the ellipse covers the runway ends, not the city centre
        assert in_noise(ap, *f.w(TYPES[want]["L"] / 2, 0)) and not in_noise(ap, 0, 0)
        print(f"  {want}: {rep['stands']} stands, {rep['taxiway_edges']} taxiway edges, {len(got['rows'])} pieces, "
              f"{len(got['lots'])} buildings, heading {ap['heading']}")
    # military base on request, none for a hamlet, none when switched off
    mil = plan_world([city], flat, None, [], bounds, {"military_base": "yes"}, {"Metro": 5000})
    assert {a["type"] for a in mil} == {"general_aviation", "military"}, mil
    assert build(mil[1], flat, {(0, 0): [0, 0]})["report"]["ok"]
    assert plan_world([city], flat, None, [], bounds, {}, {"Metro": 500}) == []
    assert plan_world([city], flat, None, [], bounds, {"airports": "none"}, {"Metro": 900000}) == []
    # a broken layout is caught: a hangar dropped on the runway
    ap = plan_world([city], flat, None, [], bounds, {}, {"Metro": 40000})[0]
    got = build(ap, flat, {(0, 0): [0, 0]})
    f = Frame(*ap["center"], ap["heading"])
    x, y = f.w(0, 0)
    got["rows"].append([x, y, 0, 0, 1, 1, 1, "hangar"])
    bad = validate(ap, f, TYPES["regional"], *got["airside"], got["rows"], got["lots"], [], (-1e9, 0), *got["landside"],
                   got["links"], [], flat, None)
    assert not bad["ok"] and any("object-free" in p for p in bad["problems"]), bad
    print("airport ok")


if __name__ == "__main__":
    _demo()
